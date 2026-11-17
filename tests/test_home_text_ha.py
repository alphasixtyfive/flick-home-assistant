"""Exercise HA adapters with small API doubles; real-HA loading is checked separately."""

import ast
from contextlib import contextmanager
from contextvars import ContextVar
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.flick_tv.const import DOMAIN
from custom_components.flick_tv.session import Registry


class TemplateError(Exception):
    pass


class Template:
    def __init__(self, source, hass):
        self.template = source
        self.hass = hass

    def ensure_valid(self):
        if self.template == "invalid syntax":
            raise TemplateError("private template source")

    def async_render(self, variables=None, *, parse_result=True, strict=False, log_fn=None):
        text = self.hass.text
        if isinstance(text, Exception):
            raise text
        if not parse_result:
            return text
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text

    def async_render_to_info(self, variables=None, strict=False, log_fn=None, **kwargs):
        result = self.async_render(variables, strict=strict, log_fn=log_fn, **kwargs)
        self.hass.last_render = {"variables": variables, "strict": strict, "log_fn": log_fn,
                                 **kwargs}
        return SimpleNamespace(result=lambda: result)


class Tracker:
    def __init__(self, hass, template, changed, strict, log_fn):
        self.hass = hass
        self.template = template
        self.changed = changed
        self.strict = strict
        self.log_fn = log_fn
        self.removed = False
        self.last = object()

    def async_refresh(self):
        if self.removed:
            return
        try:
            value = self.template.async_render_to_info(strict=self.strict, log_fn=self.log_fn).result()
        except TemplateError as error:
            value = error
        if value != self.last:
            self.changed(None, [SimpleNamespace(result=value)])
        self.last = value

    def async_remove(self):
        self.removed = True


def track(hass, templates, changed, strict=False, log_fn=None):
    if not getattr(changed, "_hass_callback", False):
        raise AssertionError("HA must deliver template changes on its event loop")
    tracker = Tracker(hass, templates[0].template, changed, strict, log_fn)
    hass.tracker = tracker
    return tracker


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}


def module(**attributes):
    result = ModuleType("test_double")
    result.__dict__.update(attributes)
    return result


def callback(function):
    function._hass_callback = True
    return function


def load(name, filename):
    path = Path(__file__).parents[1] / "custom_components" / "flick_tv" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


@contextmanager
def adapters():
    identity = lambda value: value
    select = lambda *args, **kwargs: SimpleNamespace(args=args, **kwargs)
    selector = module(TextSelector=select, TextSelectorConfig=select,
        TextSelectorType=SimpleNamespace(PASSWORD="password", TEXT="text"),
        SelectSelector=select, SelectSelectorConfig=select, BooleanSelector=select,
        TemplateSelector=select)
    schema = module(Schema=identity, Required=lambda key, **kwargs: key,
        Optional=lambda key, **kwargs: key, All=lambda *args: args, Range=lambda **kwargs: kwargs,
        Any=lambda *args: args)
    websocket_api = module(websocket_command=lambda schema: identity)
    doubles = {
        "probatio": schema,
        "homeassistant": module(),
        "homeassistant.core": module(callback=callback),
        "homeassistant.exceptions": module(TemplateError=TemplateError),
        "homeassistant.config_entries": module(ConfigFlow=Flow, OptionsFlow=Flow),
        "homeassistant.setup": module(async_setup_component=AsyncMock(return_value=True)),
        "homeassistant.helpers": module(selector=selector),
        "homeassistant.helpers.http": module(current_request=ContextVar("request", default=None)),
        "homeassistant.helpers.template": module(Template=Template),
        "homeassistant.helpers.event": module(
            TrackTemplate=lambda template, variables: SimpleNamespace(template=template, variables=variables),
            async_track_template_result=track),
        "homeassistant.components": module(websocket_api=websocket_api),
    }
    with patch.dict(sys.modules, doubles):
        renderer = load("custom_components.flick_tv.home_text_template", "home_text_template.py")
        flow = load("custom_components.flick_tv.config_flow", "config_flow.py")
        socket = load("custom_components.flick_tv.websocket", "websocket.py")
        yield renderer, flow, socket


class TemplateAdapterTests(unittest.TestCase):
    def test_tracker_compares_exact_text_including_equal_native_numbers(self):
        with adapters() as (renderer, _, _):
            hass = SimpleNamespace(text="12")
            values = []
            remove = renderer.track_home_text(hass, "source", lambda text, error: values.append((text, error)))
            self.assertEqual([("12", False)], values)
            hass.text = "12.0"
            hass.tracker.async_refresh()
            self.assertEqual(("12.0", False), values[-1])
            remove()
            self.assertTrue(hass.tracker.removed)

    def test_preview_and_live_template_preserve_literal_output(self):
        with adapters() as (renderer, _, _):
            for text in ("0012", "True", "False", "{'x': 1}", "['a']", "😀\nsecond line", ""):
                hass = SimpleNamespace(text=text)
                self.assertEqual(text, renderer.validate_home_text_template(hass, "source"))
                values = []
                remove = renderer.track_home_text(hass, "source", lambda value, error: values.append((value, error)))
                self.assertEqual([(text, False)], values)
                remove()

    def test_public_render_parameters_are_forwarded_and_template_repr_is_private(self):
        with adapters() as (renderer, _, _):
            hass = SimpleNamespace(text="literal")
            template = renderer.PlainTextTemplate("private source", hass)
            log = lambda *args: None
            template.async_render_to_info({"variable": "value"}, strict=True, log_fn=log)
            self.assertEqual({"variable": "value"}, hass.last_render["variables"])
            self.assertTrue(hass.last_render["strict"])
            self.assertIs(log, hass.last_render["log_fn"])
            self.assertFalse(hass.last_render["parse_result"])
            self.assertNotIn("private source", repr(template))
            self.assertNotIn("private source", str(template))

    def test_template_error_clears_and_later_recovery_returns_text(self):
        with adapters() as (renderer, _, _):
            hass = SimpleNamespace(text="value")
            values = []
            remove = renderer.track_home_text(hass, "source", lambda value, error: values.append((value, error)))
            hass.text = TemplateError("private source")
            hass.tracker.async_refresh()
            self.assertTrue(values[-1][1])
            hass.text = "recovered"
            hass.tracker.async_refresh()
            self.assertEqual(("recovered", False), values[-1])
            remove()


class OptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    def theme_flow(self, flow_module, *, supported=True, response=None):
        available = response if response is not None else {
            "selected": "midnight", "themes": [
                {"id": "midnight", "name": "Midnight"}, {"id": "warm", "name": "Warm"}]}
        device = SimpleNamespace(connected=True,
            status={"capabilities": ["themes"] if supported else []},
            request=AsyncMock(side_effect=[available, {}]))
        options = {"home_text": {"template": "keep this"}, "future_option": True}
        entry = SimpleNamespace(options=options, runtime_data=device)
        flow = flow_module.FlickOptionsFlow("entry")
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _: entry))
        return flow, device, {"home_text": options["home_text"]}

    async def test_theme_menu_is_available_only_when_tv_advertises_support(self):
        with adapters() as (_, flow_module, _):
            for supported in (True, False):
                flow, device, _ = self.theme_flow(flow_module, supported=supported)
                menu = await flow.async_step_init()
                self.assertEqual(supported, "theme" in menu["menu_options"])
                device.request.assert_not_called()
            result = await flow.async_step_theme()
            self.assertEqual("theme_unavailable", result["reason"])

    async def test_theme_form_reads_the_current_tv_selection_and_preserves_options(self):
        with adapters() as (_, flow_module, _):
            flow, device, options = self.theme_flow(flow_module)
            result = await flow.async_step_theme({"theme_id": "warm"})
            self.assertEqual("create_entry", result["type"])
            self.assertEqual(options, result["data"])
            self.assertEqual([("list_themes",), ("set_theme", {"id": "warm"})],
                [call.args for call in device.request.await_args_list])

    async def test_removed_theme_is_not_sent_as_a_command(self):
        with adapters() as (_, flow_module, _):
            flow, device, _ = self.theme_flow(flow_module)
            result = await flow.async_step_theme({"theme_id": "removed-theme"})
            self.assertEqual({"theme_id": "theme_unavailable"}, result["errors"])
            device.request.assert_awaited_once_with("list_themes")

    async def test_imported_theme_is_applied_without_shortening_its_identifier(self):
        imported_id = "custom_01234567-89ab-4def-8123-456789abcdef"
        with adapters() as (_, flow_module, _):
            flow, device, options = self.theme_flow(flow_module, response={
                "selected": imported_id, "themes": [{"id": imported_id, "name": "Ocean"}]})
            result = await flow.async_step_theme({"theme_id": imported_id})
            self.assertEqual(options, result["data"])
            device.request.assert_awaited_with("set_theme", {"id": imported_id})

    async def test_malformed_theme_list_fails_without_applying_a_theme(self):
        with adapters() as (_, flow_module, _):
            flow, device, _ = self.theme_flow(flow_module, response={"selected": "missing", "themes": []})
            result = await flow.async_step_theme({"theme_id": "warm"})
            self.assertEqual("command_failed", result["reason"])
            device.request.assert_awaited_once_with("list_themes")

    async def test_denied_theme_update_stays_in_form_and_does_not_save_options(self):
        with adapters() as (_, flow_module, _):
            flow, device, _ = self.theme_flow(flow_module)
            from custom_components.flick_tv.session import DeviceRejected
            device.request.side_effect = [
                {"selected": "midnight", "themes": [{"id": "midnight", "name": "Midnight"},
                                                     {"id": "warm", "name": "Warm"}]},
                DeviceRejected("private failure"),
            ]
            result = await flow.async_step_theme({"theme_id": "warm"})
            self.assertEqual("form", result["type"])
            self.assertEqual({"base": "command_failed"}, result["errors"])
            self.assertNotIn("private failure", str(result))

    def create_flow(self, flow_module, text="Cinema 🎬"):
        existing = {"home_text": {"template": "existing",
                                        "show_for_children": False}, "future_option": True}
        entry = SimpleNamespace(options=existing)
        flow = flow_module.FlickOptionsFlow("entry")
        flow.hass = SimpleNamespace(text=text,
            config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entry))
        flow._device = lambda: SimpleNamespace(request=AsyncMock(return_value={}))
        return flow, existing

    async def test_root_menu_groups_profiles_without_mixing_profile_actions(self):
        with adapters() as (_, flow_module, _):
            flow, _ = self.create_flow(flow_module)
            root = await flow.async_step_init()
            self.assertEqual("menu", root["type"])
            self.assertEqual("init", root["step_id"])
            self.assertEqual(["home_text", "wallpaper", "profiles"], root["menu_options"])
            profiles = await flow.async_step_profiles()
            self.assertEqual("menu", profiles["type"])
            self.assertEqual("profiles", profiles["step_id"])
            self.assertEqual(["add_profile", "edit_profile", "parent_pin", "delete_profile"],
                             profiles["menu_options"])
            for step in [*root["menu_options"], *profiles["menu_options"]]:
                self.assertTrue(callable(getattr(flow, f"async_step_{step}")))

    async def test_home_text_is_saved_without_requiring_an_online_tv(self):
        with adapters() as (_, flow_module, _):
            flow, existing = self.create_flow(flow_module)
            flow._device = lambda: self.fail("HomeText configuration must work offline")
            result = await flow.async_step_home_text({"template": "new",
                                                           "show_for_children": False})
            self.assertEqual("new", result["data"]["home_text"]["template"])
            self.assertNotIn("future_option", result["data"])
            self.assertEqual("existing", existing["home_text"]["template"])

    async def test_every_options_menu_item_has_a_published_label(self):
        component = Path(__file__).parents[1] / "custom_components" / "flick_tv"
        source = json.loads((component / "strings.json").read_text(encoding="utf-8"))
        published = json.loads((component / "translations" / "en.json").read_text(encoding="utf-8"))
        with adapters() as (_, flow_module, _):
            flow, _ = self.create_flow(flow_module)
            with patch.object(flow, "_supports_themes", return_value=True):
                menus = [await flow.async_step_init(), await flow.async_step_profiles()]
            for menu in menus:
                step = menu["step_id"]
                labels = published["options"]["step"][step]["menu_options"]
                self.assertEqual(set(menu["menu_options"]), set(labels))
                self.assertEqual(source["options"]["step"][step]["menu_options"], labels)
                for option in menu["menu_options"]:
                    self.assertIsInstance(labels[option], str)
                    self.assertTrue(labels[option].strip())

    async def test_form_has_one_message_editor(self):
        with adapters() as (_, flow_module, _):
            flow, _ = self.create_flow(flow_module)
            result = await flow.async_step_home_text()
            self.assertEqual({"template", "show_for_children"}, result["data_schema"].keys())
            saved = await flow.async_step_home_text({"template": "Message"})
            self.assertEqual({"template": "Message", "show_for_children": False},
                             saved["data"]["home_text"])

    async def test_saving_message_replaces_unowned_options_with_current_schema(self):
        with adapters() as (_, flow_module, _):
            flow, _ = self.create_flow(flow_module)
            entry = flow.hass.config_entries.async_get_entry("entry")
            entry.options = {"unowned_setting": True}
            result = await flow.async_step_home_text({"template": "Message"})
            self.assertEqual({"home_text": {"template": "Message", "show_for_children": False}},
                             result["data"])
            self.assertEqual({"unowned_setting": True}, entry.options)

    async def test_empty_message_saves_without_rendering(self):
        with adapters() as (_, flow_module, _):
            flow, _ = self.create_flow(flow_module)
            with patch.object(flow_module, "validate_home_text_template", side_effect=AssertionError("Unexpected render")):
                result = await flow.async_step_home_text({"template": "  "})
            self.assertEqual({"template": "  ", "show_for_children": False}, result["data"]["home_text"])

    async def test_invalid_template_stays_in_form_with_generic_error(self):
        with adapters() as (_, flow_module, _):
            flow, _ = self.create_flow(flow_module)
            result = await flow.async_step_home_text({"template": "invalid syntax"})
            self.assertEqual({"template": "invalid_template"}, result["errors"])
            self.assertNotIn("private template source", str(result))

    async def test_other_option_actions_keep_only_current_options(self):
        with adapters() as (_, flow_module, _):
            for step, data in (("profile", {"name": "Name", "child": True}),
                               ("parent_pin", {"profile_id": "adult", "pin": "7391"}),
                               ("delete_profile", {"profile_id": "child"}),
                               ("wallpaper", {"shuffle": True})):
                flow, existing = self.create_flow(flow_module)
                flow._editing = {"id": "child", "name": "Name", "child": True}
                result = await getattr(flow, f"async_step_{step}")(data)
                self.assertEqual({"home_text": existing["home_text"]}, result["data"])
                self.assertNotIn("7391", str(result))


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.events = []
        self.results = []
        self.errors = []
        self.command_subscription = object()
        self.connection = SimpleNamespace(
            user=SimpleNamespace(id="tv-user", is_active=True, is_admin=False),
            subscriptions={1: self.command_subscription},
            send_event=lambda message_id, value: self.events.append((message_id, value)),
            send_result=lambda message_id: self.results.append(message_id),
            send_error=lambda message_id, code, message: self.errors.append((message_id, code)),
        )
        data = {"device_id": "tv-1", "device_secret": "a" * 64, "name": "Test TV",
                "protocol": 1, "pairing_code": "123456"}
        self.registry.connect(data, "tv-user", self.connection, lambda event: None)
        self.device = self.registry.register(self.registry.pairing("123456").entry_data())
        self.hass = SimpleNamespace(data={DOMAIN: self.registry})
        self.subscriptions = []

        def subscribe(profile_id, generation, send):
            value = SimpleNamespace(profile_id=profile_id, generation=generation,
                                    send=send, closed=False)
            value.close = lambda: setattr(value, "closed", True)
            self.subscriptions.append(value)
            return value

        self.device.home_text = SimpleNamespace(subscribe=subscribe)
        self.message = {"id": 2, "type": "flick_tv/home_text", "device_id": "tv-1",
                        "device_secret": "a" * 64, "profile_id": "adult", "profile_generation": 4}

    def test_optional_subscription_keeps_command_channel_separate(self):
        with adapters() as (_, _, socket):
            socket.ws_home_text(self.hass, self.connection, self.message)
            self.assertEqual([2], self.results)
            self.assertIs(self.command_subscription, self.connection.subscriptions[1])
            payload = {"profile_id": "adult", "profile_generation": 4, "revision": 1,
                       "text": "Message", "show_for_children": False}
            self.subscriptions[-1].send(payload)
            self.assertEqual([(2, payload)], self.events)
            self.connection.subscriptions[2]()
            self.assertTrue(self.subscriptions[-1].closed)

    def test_main_subscription_captures_only_its_public_request_transport(self):
        with adapters() as (_, _, socket):
            from homeassistant.helpers.http import current_request

            closed = []
            transport = SimpleNamespace(close=lambda: closed.append(True))
            token = current_request.set(SimpleNamespace(transport=transport))
            other = SimpleNamespace(**{**vars(self.connection), "subscriptions": {}})
            other.send_result = lambda message_id, result=None: self.results.append(message_id)
            try:
                socket.ws_connect(self.hass, other, {"id": 1, "type": "flick_tv/connect",
                    "device_id": "tv-1", "device_secret": "a" * 64,
                    "name": "Test TV", "protocol": 1})
            finally:
                current_request.reset(token)
            self.assertEqual([1], self.results)
            self.assertEqual([], closed)
            self.registry.unregister("tv-1")
            self.assertEqual([True], closed)

    def test_wrong_secret_and_old_socket_cannot_subscribe(self):
        with adapters() as (_, _, socket):
            socket.ws_home_text(self.hass, self.connection,
                                       {**self.message, "device_secret": "b" * 64})
            other = SimpleNamespace(**{**vars(self.connection), "subscriptions": {}})
            socket.ws_home_text(self.hass, other, self.message)
            self.assertEqual([], self.subscriptions)
            self.assertEqual([(2, "invalid_home_text")] * 2, self.errors)

    def test_main_connection_needs_no_home_text_subscription(self):
        self.assertTrue(self.device.connected)
        self.assertEqual([], self.subscriptions)
        self.assertEqual({1: self.command_subscription}, self.connection.subscriptions)

    def test_not_ready_manager_returns_only_optional_subscription_error(self):
        with adapters() as (_, _, socket):
            self.device.home_text = None
            socket.ws_home_text(self.hass, self.connection, self.message)
            self.assertEqual([(2, "invalid_home_text")], self.errors)
            self.assertEqual([], self.results)
            self.assertIs(self.command_subscription, self.connection.subscriptions[1])


class EntryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def entry_setup(self, options):
        data = {"device_id": "tv-1", "device_secret": "a" * 64, "name": "Test TV",
                "protocol": 1, "user_id": "tv-user"}
        entry = SimpleNamespace(data=data, options=options, async_on_unload=lambda remove: None,
                                add_update_listener=lambda listener: lambda: None)
        hass = SimpleNamespace(data={DOMAIN: Registry()}, config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True)))
        return hass, entry
    async def test_failed_platform_setup_closes_home_text_manager(self):
        from custom_components.flick_tv import async_setup_entry

        with adapters():
            hass, entry = self.entry_setup({})
            hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError("setup failed")
            with self.assertRaises(RuntimeError):
                await async_setup_entry(hass, entry)
            self.assertIsNone(entry.runtime_data.home_text)
            self.assertEqual(set(), entry.runtime_data._listeners)

    async def test_options_update_during_setup_or_unload_has_no_manager_to_configure(self):
        from custom_components.flick_tv import async_options_updated

        await async_options_updated(None, SimpleNamespace(options={}))
        await async_options_updated(None, SimpleNamespace(
            options={}, runtime_data=SimpleNamespace(home_text=None)))
