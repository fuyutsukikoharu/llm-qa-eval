"""配置加载：把 YAML 变成强类型对象，路径统一相对配置文件解析。

为什么要单独一层配置：
- 评测要能在「同一批用例」上切换模型、prompt、检索参数，配置和代码必须解耦，
  否则每换一个模型就得改代码，无法做 A/B 对比。
- 所有路径相对 configs/*.yaml 解析，保证从任何目录运行结果一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class RetrievalOptions:
    top_k: int = 3
    min_score: float = 0.12
    expand_synonyms: bool = False


@dataclass
class GuardrailOptions:
    enabled: bool = False
    block_injection: bool = True
    block_pii_requests: bool = True
    block_abuse_requests: bool = True
    escalate_compensation: bool = True
    redact_pii: bool = True
    require_citation: bool = False
    medical_escalation: bool = True


@dataclass
class JudgeOptions:
    provider: str = "heuristic"  # heuristic | openai
    model: str = "mock-judge"
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.0


@dataclass
class RunConfig:
    run_name: str
    project_root: Path
    cases_path: Path
    kb_dir: Path
    system_prompt_path: Path
    out_dir: Path
    provider: str = "mock"
    model: str = "mock-baseline"
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.2
    max_tokens: int = 512
    timeout_s: float = 60.0
    retries: int = 2
    concurrency: int = 4
    pass_threshold: float = 0.8
    retrieval: RetrievalOptions = field(default_factory=RetrievalOptions)
    guardrails: GuardrailOptions = field(default_factory=GuardrailOptions)
    judge: JudgeOptions = field(default_factory=JudgeOptions)
    prompt_text: str = ""

    def _relative(self, path: Path) -> str:
        """报告里不写本机绝对路径，避免把用户名/目录结构带到公开仓库。"""
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return path.name

    def to_dict(self) -> dict[str, Any]:
        """给报告用的元信息，注意不要把 API key 写进去（这里只记录环境变量名）。"""
        return {
            "run_name": self.run_name,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "pass_threshold": self.pass_threshold,
            "concurrency": self.concurrency,
            "retrieval": self.retrieval.__dict__,
            "guardrails": self.guardrails.__dict__,
            "judge": {"provider": self.judge.provider, "model": self.judge.model},
            "system_prompt": self._relative(self.system_prompt_path),
            "cases": self._relative(self.cases_path),
            "kb": self._relative(self.kb_dir),
        }


def _resolve(base: Path, value: str) -> Path:
    return (base / value).resolve()


def load_config(path: str | Path) -> RunConfig:
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在：{config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    base = config_path.parent

    paths = raw.get("paths") or {}
    for key in ("cases", "kb", "system_prompt", "out_dir"):
        if key not in paths:
            raise ConfigError(f"配置缺少 paths.{key}")

    retrieval = RetrievalOptions(**(raw.get("retrieval") or {}))
    guardrails = GuardrailOptions(**(raw.get("guardrails") or {}))
    judge = JudgeOptions(**(raw.get("judge") or {}))

    system_prompt_path = _resolve(base, paths["system_prompt"])
    if not system_prompt_path.exists():
        raise ConfigError(f"系统提示词不存在：{system_prompt_path}")

    provider = raw.get("provider", "mock")
    if provider not in {"mock", "openai"}:
        raise ConfigError(f"provider 只支持 mock / openai，收到 {provider}")
    if judge.provider not in {"heuristic", "openai"}:
        raise ConfigError(f"judge.provider 只支持 heuristic / openai，收到 {judge.provider}")
    if judge.provider == "openai" and not judge.model:
        raise ConfigError("judge.provider=openai 时必须配置 judge.model")

    cfg = RunConfig(
        run_name=raw.get("run_name") or config_path.stem,
        project_root=base.parent,
        cases_path=_resolve(base, paths["cases"]),
        kb_dir=_resolve(base, paths["kb"]),
        system_prompt_path=system_prompt_path,
        out_dir=_resolve(base, paths["out_dir"]),
        provider=provider,
        model=raw.get("model") or ("mock-baseline" if provider == "mock" else ""),
        base_url=raw.get("base_url", ""),
        api_key_env=raw.get("api_key_env", "OPENAI_API_KEY"),
        temperature=float(raw.get("temperature", 0.2)),
        max_tokens=int(raw.get("max_tokens", 512)),
        timeout_s=float(raw.get("timeout_s", 60)),
        retries=int(raw.get("retries", 2)),
        concurrency=max(1, int(raw.get("concurrency", 4))),
        pass_threshold=float((raw.get("report") or {}).get("pass_threshold", 0.8)),
        retrieval=retrieval,
        guardrails=guardrails,
        judge=judge,
        prompt_text=system_prompt_path.read_text(encoding="utf-8"),
    )
    return cfg
