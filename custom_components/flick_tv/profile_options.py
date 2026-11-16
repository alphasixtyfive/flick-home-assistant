"""Choices for the profile editor."""


def app_options(installed: list[dict[str, str]], allowed: list[str]) -> list[dict[str, str]]:
    options = {}
    for item in installed:
        options.setdefault(item["value"], dict(item))
    for package in allowed:
        options.setdefault(package, {"value": package, "label": f"{package} (not installed)"})
    return list(options.values())
