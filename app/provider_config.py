"""Provider profiles isolate credentials; old installations stay on LM Studio."""
DEFAULTS = {
    "lmstudio": {"url": "http://127.0.0.1:1234", "model": "qwen/qwen3.8-27b", "mode": "auto", "token": ""},
    "openrouter": {"model": "openrouter/free", "mode": "auto", "token": ""},
    "google": {"model": "gemini-3.8-flash", "mode": "auto", "token": ""},
    "huggingface": {"model": "", "mode": "auto", "token": ""},
}
LABELS = {"lmstudio": "LM Studio", "openrouter": "OpenRouter", "google": "Google AI", "huggingface": "Hugging Face"}
GOOGLE_FREE_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite")


def profiles(config):
    result = {name: dict(values) for name, values in DEFAULTS.items()}
    # Legacy flat LM Studio configuration is read without rewriting the database.
    result["lmstudio"].update({k: config[k] for k in DEFAULTS["lmstudio"] if k in config})
    for name, values in config.get("providers", {}).items():
        if name in result:
            result[name].update({k: v for k, v in values.items() if k in result[name]})
    return result


def selected_config(config):
    name = config.get("provider", "lmstudio")
    if name not in DEFAULTS:
        raise ValueError("Unknown AI provider")
    return {**profiles(config)[name], "provider": name, "timeout": config.get("timeout", 300)}


def public_settings(config):
    all_profiles = profiles(config)
    visible = {name: {k: v for k, v in values.items() if k != "token"} | {"has_token": bool(values.get("token"))}
               for name, values in all_profiles.items()}
    selected = config.get("provider", "lmstudio")
    # Preserve the existing response fields for older clients.
    return {**visible[selected], "provider": selected, "providers": visible, "timeout": config.get("timeout", 300),
            "auto_approve": config.get("auto_approve", False), "google_models": list(GOOGLE_FREE_MODELS)}


def settings_update(config, body, validate_url):
    name = body.get("provider", "lmstudio")
    if name not in DEFAULTS:
        raise ValueError("Choose a supported provider")
    all_profiles = profiles(config)
    profile = dict(all_profiles[name])
    model = body.get("model", profile["model"])
    if not isinstance(model, str) or not model.strip() or len(model) > 300:
        raise ValueError("Enter a model identifier")
    profile["model"] = model.strip()
    profile["mode"] = body.get("mode", profile["mode"])
    timeout = body.get("timeout", config.get("timeout", 300))
    if profile["mode"] not in ("auto", "vision", "ocr"):
        raise ValueError("Choose a valid extraction mode")
    if type(timeout) is not int or not 30 <= timeout <= 600:
        raise ValueError("Timeout must be 30–600 seconds")
    if name == "lmstudio":
        profile["url"] = validate_url(body.get("url", profile["url"]))
    else:
        if body.get("external_confirmed") is not True:
            raise ValueError("Confirm external processing and the free-account requirements")
        if name == "openrouter" and profile["model"] != "openrouter/free" and not profile["model"].endswith(":free"):
            raise ValueError("OpenRouter is restricted to openrouter/free or a model ending in :free")
        if name == "google" and profile["model"] not in GOOGLE_FREE_MODELS:
            raise ValueError("Choose one of the listed Google free-tier models")
    if body.get("clear_token"):
        profile["token"] = ""
    elif body.get("token"):
        token = body["token"]
        if not isinstance(token, str) or len(token) > 2000 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("Invalid API key or token")
        profile["token"] = token
    if name != "lmstudio" and not profile["token"] and not body.get("clear_token"):
        raise ValueError("An API key or token is required for this provider")
    auto_approve = body.get("auto_approve", config.get("auto_approve", False))
    if type(auto_approve) is not bool:
        raise ValueError("Auto-approve must be enabled or disabled")
    all_profiles[name] = profile
    return {"provider": name, "providers": all_profiles, "timeout": timeout, "auto_approve": auto_approve}
