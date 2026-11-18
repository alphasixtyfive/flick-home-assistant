"""Choices for the profile editor."""

from collections import Counter


def profile_choices(profiles: list[dict[str, str]]) -> dict[str, str]:
    counts = Counter(item["name"] for item in profiles)
    names = {name for name, count in counts.items() if count == 1}
    choices = {}
    for item in profiles:
        name, profile_id = item["name"], item["id"]
        label = name
        if counts[name] > 1:
            label = f"{name} ({profile_id})"
            while label in names:
                label += " (profile)"
            names.add(label)
        choices[label] = profile_id
    return choices


def app_options(installed: list[dict[str, str]], allowed: list[str]) -> list[dict[str, str]]:
    options = {}
    for item in installed:
        options.setdefault(item["value"], dict(item))
    for package in allowed:
        options.setdefault(package, {"value": package, "label": f"{package} (not installed)"})
    return list(options.values())
