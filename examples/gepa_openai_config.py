"""Real-provider GEPAConfig for OpenAI (GPT-5.6 family).

This builds the config consumed by GEPAOptimizer; it makes no network calls
and reads no secret value itself. credential_env only names the environment
variable GEPAOptimizer.optimize() reads the API key from at call time.

model (gpt-5.6-terra) is called once per metric call during evaluation/search,
so it favors cost; reflection_model (gpt-5.6-sol) is called far less often by
GEPA to rewrite instructions, so it favors reasoning quality. temperature is
left at GEPAConfig's default (1.0): both are OpenAI reasoning-tier models,
and DSPy requires temperature=1.0 (or None) together with max_tokens >= 16000
(or None) for this model family; max_tokens below that threshold fails before
any network call.
"""

from prompt_optimizer.adapters.dspy.gepa_optimizer import GEPAConfig


def build_gepa_config() -> GEPAConfig:
    return GEPAConfig(
        model="openai/gpt-5.6-terra",
        reflection_model="openai/gpt-5.6-sol",
        credential_env="OPENAI_API_KEY",
        max_tokens=16000,
    )
