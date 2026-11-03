"""评测执行器：串起「检索 -> 模型 -> 断言 -> 打分」的主流程。

关键设计：
- 一个用例可以有多轮（turns），模型上一轮的回答会回填成 assistant 消息；
- 一个用例可以重复采样（repeat），用于一致性/稳定性检查；
- 检索用「最近 2 轮用户消息」拼接，避免多轮里的省略和代词导致召回丢失；
- 组内一致性（group）在所有用例跑完后统一判定，因此需要两阶段。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .analysis import diagnose
from .checks import CheckContext, CheckResult, evaluate
from .config import RunConfig
from .guardrails import post_guard, pre_guard
from .judges import Judge
from .loader import Case
from .models import ChatModel
from .retrieval import Chunk, Retriever
from .text import extract_fact_tokens, truncate

_PRINT_LOCK = threading.Lock()


@dataclass
class SampleResult:
    answer: str
    retrieved_ids: list[str]
    retrieved_text: str
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    blocked: bool = False
    error: str | None = None


@dataclass
class CaseResult:
    case: Case
    samples: list[SampleResult]
    checks: list[CheckResult] = field(default_factory=list)
    score: float = 0.0
    passed: bool = False
    error: str | None = None
    retrieval_hit: bool = False

    @property
    def final_answer(self) -> str:
        return self.samples[-1].answer if self.samples else ""

    @property
    def cause_tags(self) -> list[dict[str, str]]:
        return diagnose(self)


@dataclass
class RunResult:
    config: RunConfig
    case_results: list[CaseResult]
    started_at: str
    duration_s: float
    warnings: list[str] = field(default_factory=list)


def build_system_prompt(template: str, chunks: list[tuple[Chunk, float]]) -> str:
    """把检索片段拼进 system prompt。片段带编号，方便模型引用、方便审计。"""
    if not chunks:
        return template + (
            "\n\n## 检索到的知识库片段\n\n（本轮没有检索到相关片段。"
            "请按工作原则第 2 条处理：明确说明暂时无法确认，并引导人工客服或订单页。）"
        )
    blocks = "\n\n".join(f"[{chunk.id}] {chunk.title}\n{chunk.text}" for chunk, _ in chunks)
    return template + "\n\n## 检索到的知识库片段（只能依据这些片段作答）\n\n" + blocks


def score_case(checks: list[CheckResult], threshold: float, error: str | None = None) -> tuple[float, bool]:
    total_weight = sum(check.weight for check in checks) or 1.0
    score = sum(check.weight * check.score for check in checks) / total_weight
    hard_fail = any(check.hard and check.score < 1.0 for check in checks)
    passed = score >= threshold and not hard_fail and error is None
    return round(score, 4), passed


class Evaluator:
    def __init__(
        self,
        cfg: RunConfig,
        cases: list[Case],
        retriever: Retriever,
        model: ChatModel,
        judge: Judge,
    ):
        self.cfg = cfg
        self.cases = cases
        self.retriever = retriever
        self.model = model
        self.judge = judge
        self.kb: dict[str, Chunk] = retriever.kb

    def run(self) -> RunResult:
        started = datetime.now(timezone.utc).astimezone()
        started_perf = time.perf_counter()
        results: list[CaseResult] = []
        total = len(self.cases)
        with ThreadPoolExecutor(max_workers=self.cfg.concurrency) as pool:
            futures = {pool.submit(self.run_case, case): case for case in self.cases}
            for index, future in enumerate(as_completed(futures), start=1):
                case = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # 单条用例异常不应拖垮整轮评测
                    result = CaseResult(
                        case=case,
                        samples=[SampleResult(answer="", retrieved_ids=[], retrieved_text="")],
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    result.score, result.passed = score_case([], self.cfg.pass_threshold, result.error)
                results.append(result)
                with _PRINT_LOCK:
                    print(f"[{index:>2}/{total}] {case.id} 完成", flush=True)
        results.sort(key=lambda item: [c.id for c in self.cases].index(item.case.id))
        self._attach_group_consistency(results)
        # 组内一致性会在所有用例跑完后回填，所以最终结果统一再打一遍，避免误导
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(f"  {result.case.id:<5} {status}  {result.score:.2f}  {truncate(result.case.title, 26)}")
        return RunResult(
            config=self.cfg,
            case_results=results,
            started_at=started.isoformat(timespec="seconds"),
            duration_s=round(time.perf_counter() - started_perf, 2),
        )

    def run_case(self, case: Case) -> CaseResult:
        samples: list[SampleResult] = []
        for _ in range(case.repeat):
            samples.append(self._run_sample(case))
        result = CaseResult(case=case, samples=samples, error=samples[0].error if samples else None)
        result.retrieval_hit = self._retrieval_hit(case, samples[0].retrieved_ids) if samples else False
        result.checks = self._evaluate_checks(case, samples)
        result.score, result.passed = score_case(result.checks, self.cfg.pass_threshold, result.error)
        return result

    def _run_sample(self, case: Case) -> SampleResult:
        history: list[dict[str, str]] = []
        user_turns: list[str] = []
        retrieved: list[tuple[Chunk, float]] = []
        answer = ""
        blocked = False
        latency = prompt_tokens = completion_tokens = 0
        error: str | None = None

        for turn in case.turns:
            user_turns.append(turn)
            # 多轮检索：把最近两轮用户消息拼起来，补全代词/省略造成的信息缺失
            query = " ".join(user_turns[-2:])
            retrieved = self.retriever.search(
                query,
                top_k=self.cfg.retrieval.top_k,
                min_score=self.cfg.retrieval.min_score,
            )
            system_prompt = build_system_prompt(self.cfg.prompt_text, retrieved)
            messages = [{"role": "system", "content": system_prompt}] + history + [
                {"role": "user", "content": turn}
            ]

            guardrail_answer = pre_guard(turn, self.cfg.guardrails) if self.cfg.guardrails.enabled else None
            if guardrail_answer is not None:
                answer, blocked = guardrail_answer, True
            else:
                response = self.model.chat(
                    messages,
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_tokens,
                )
                answer = response.text
                latency += response.latency_ms
                prompt_tokens += response.prompt_tokens
                completion_tokens += response.completion_tokens
                if response.error:
                    error = response.error
                    answer = answer or ""

            if self.cfg.guardrails.enabled and answer and not error:
                answer = post_guard(
                    turn,
                    answer,
                    [chunk.id for chunk, _ in retrieved],
                    self.kb,
                    self.cfg.guardrails,
                )

            history.append({"role": "user", "content": turn})
            history.append({"role": "assistant", "content": answer})

        return SampleResult(
            answer=answer,
            retrieved_ids=[chunk.id for chunk, _ in retrieved],
            retrieved_text="\n\n".join(f"[{chunk.id}] {chunk.text}" for chunk, _ in retrieved),
            latency_ms=latency,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            blocked=blocked,
            error=error,
        )

    def _evaluate_checks(self, case: Case, samples: list[SampleResult]) -> list[CheckResult]:
        checks: list[CheckResult] = []
        answers = [sample.answer for sample in samples]
        for spec in case.checks:
            if spec.type == "llm_judge":
                checks.append(self._judge_check(case, spec, samples))
                continue
            contexts = [
                CheckContext(
                    response=sample.answer,
                    case=case,
                    retrieved_ids=sample.retrieved_ids,
                    retrieved_text=sample.retrieved_text,
                    kb=self.kb,
                    samples=answers,
                )
                for sample in samples
            ]
            per_sample = [evaluate(spec, context) for context in contexts]
            score = sum(item.score for item in per_sample) / len(per_sample)
            details = list(dict.fromkeys(item.detail for item in per_sample))
            checks.append(
                CheckResult(
                    check_type=spec.type,
                    passed=all(item.passed for item in per_sample),
                    score=round(score, 4),
                    weight=spec.weight,
                    detail="；".join(details),
                    hard=spec.hard,
                )
            )
        return checks

    def _judge_check(self, case: Case, spec: Any, samples: list[SampleResult]) -> CheckResult:
        question = "\n".join(case.turns)
        results = [
            self.judge.score(
                str(spec.params["rubric"]),
                question,
                sample.answer,
                sample.retrieved_text,
            )
            for sample in samples
        ]
        min_score = float(spec.params["min_score"])
        scores = [item.score for item in results]
        normalized = sum(scores) / (5 * len(scores))
        reason = "；".join(dict.fromkeys(item.reason for item in results))
        return CheckResult(
            check_type="llm_judge",
            passed=all(score >= min_score for score in scores),
            score=round(normalized, 4),
            weight=spec.weight,
            detail=f"judge={scores}（门槛 {min_score}）：{truncate(reason, 120)}",
            hard=spec.hard,
        )

    def _retrieval_hit(self, case: Case, retrieved_ids: list[str]) -> bool:
        if not case.source:
            return True  # 安全/开放类用例没有唯一正确文档，不计入检索命中率
        expected_doc = case.source.split("-")[0]
        return any(doc == expected_doc for doc in (item.split("-")[0] for item in retrieved_ids))

    def _attach_group_consistency(self, results: list[CaseResult]) -> None:
        """组内一致性：同一政策的不同问法，答案里的「数字+单位」必须完全一致。"""
        groups: dict[str, list[CaseResult]] = {}
        for result in results:
            if result.case.group:
                groups.setdefault(result.case.group, []).append(result)

        for group, members in groups.items():
            signatures = {
                member.case.id: extract_fact_tokens(member.final_answer) for member in members
            }
            unique = {tuple(signature) for signature in signatures.values()}
            consistent = len(unique) == 1 and all(signatures.values())
            detail = (
                f"组 {group} 事实签名一致：{signatures[members[0].case.id]}"
                if consistent
                else f"组 {group} 事实签名不一致：{signatures}"
            )
            for member in members:
                member.checks.append(
                    CheckResult(
                        check_type="group_consistency",
                        passed=consistent,
                        score=1.0 if consistent else 0.0,
                        weight=member.case.group_weight,
                        detail=detail,
                    )
                )
                member.score, member.passed = score_case(
                    member.checks, self.cfg.pass_threshold, member.error
                )
