"""Home Assistant's template tracker, kept out of the display protocol."""

from homeassistant.core import callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import TrackTemplate, async_track_template_result
from homeassistant.helpers.template import Template


def _private_log(*args, **kwargs):
    """Template errors belong in the options form, not integration logs."""


class PlainTextTemplate(Template):
    """Use HA's public rendering API while retaining exact text comparisons."""

    def __repr__(self):
        return "PlainTextTemplate(<redacted>)"

    def __str__(self):
        return self.__repr__()

    @callback
    def async_render_to_info(self, variables=None, strict=False, log_fn=None, **kwargs):
        kwargs["parse_result"] = False
        return super().async_render_to_info(
            variables, strict=strict, log_fn=log_fn or _private_log, **kwargs)


def validate_home_text_template(hass, source):
    template = PlainTextTemplate(source, hass)
    template.ensure_valid()
    return template.async_render(parse_result=False, strict=True, log_fn=_private_log)


def track_home_text(hass, source, observed):
    template = PlainTextTemplate(source, hass)

    @callback
    def changed(event, updates):
        for update in updates:
            observed(update.result, isinstance(update.result, TemplateError))

    tracker = None
    try:
        tracker = async_track_template_result(
            hass, [TrackTemplate(template, None)], changed, strict=True, log_fn=_private_log)
        tracker.async_refresh()
    except TemplateError:
        if tracker is not None:
            tracker.async_remove()
        observed(None, True)
        return lambda: None
    return tracker.async_remove
