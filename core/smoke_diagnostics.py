"""Safe, non-persistent formatting for controlled smoke-test observations."""
from collections.abc import Mapping


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _value(source, name, default=None):
    try:
        return getattr(source, name, default) if source is not None else default
    except Exception:
        return default


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def format_smoke_diagnostic(exact_intent=None, resolver_intent=None, provider_response=None,
                            validation=None, router_calls=None, plugin_calls=None,
                            tts_ready=None, final_state=None, provider_latency_ms=None,
                            total_latency_ms=None, production_error=None):
    """Return a structural report without raising or retaining response text/secrets.

    A runtime exception is represented separately so optional diagnostic absence never
    masquerades as a provider, router, or browser failure.
    """
    validator=_mapping(validation)
    error_type=type(production_error).__name__ if isinstance(production_error, BaseException) else None
    return {
        "exact_parser":exact_intent if isinstance(exact_intent, str) else "NOT_AVAILABLE",
        "resolver":resolver_intent if isinstance(resolver_intent, str) else "NOT_AVAILABLE",
        "provider_intent":_value(provider_response, "intent", "NOT_AVAILABLE"),
        "provider_validation":validator.get("accepted", "NOT_AVAILABLE"),
        "provider_validation_category":validator.get("failure_category", "NOT_AVAILABLE"),
        "router_calls":_count(router_calls),
        "plugin_calls":_count(plugin_calls),
        "tts_ready":tts_ready if isinstance(tts_ready, bool) else "NOT_AVAILABLE",
        "final_state":final_state if isinstance(final_state, str) else "NOT_AVAILABLE",
        "provider_latency_ms":provider_latency_ms if isinstance(provider_latency_ms, int) and provider_latency_ms >= 0 else None,
        "total_latency_ms":total_latency_ms if isinstance(total_latency_ms, int) and total_latency_ms >= 0 else None,
        "production_error":error_type,
    }
