# -*- coding: utf-8 -*-
"""颜色判定、采样聚合与配置解析的回归测试。

不联网、不创建托盘图标和任务栏挂件，全部走纯函数，可以在任何机器上跑：

    python -m unittest -v test_colors

重点覆盖三处容易改坏的地方：
* 阈值边界（`80` 算绿、`80.1` 算黄，别写成 `<`）；
* 最近 3 次采样的中位数 + 丢包升档；
* 两套挂件配色的对比度（改颜色时必须一起改这里，否则亮度不够会看不见）。
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from collections import deque

import main as m


def _relative_luminance(rgb):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(color_a, color_b):
    """WCAG 2.x 对比度，1~21。"""
    hi, lo = sorted((_relative_luminance(color_a), _relative_luminance(color_b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class LevelTests(unittest.TestCase):
    THRESHOLDS = (80.0, 150.0)

    def test_default_boundaries(self):
        cases = [(0, m.GOOD), (80, m.GOOD), (80.1, m.FAIR),
                 (150, m.FAIR), (150.1, m.BAD), (5000, m.BAD)]
        for value, want in cases:
            with self.subTest(value=value):
                self.assertEqual(m.level_of(value, self.THRESHOLDS), want)

    def test_tight_thresholds_for_lan(self):
        thresholds = (5.0, 20.0)
        self.assertEqual(m.level_of(3, thresholds), m.GOOD)
        self.assertEqual(m.level_of(12, thresholds), m.FAIR)
        self.assertEqual(m.level_of(60, thresholds), m.BAD)


class SummarizeTests(unittest.TestCase):
    THRESHOLDS = (80.0, 150.0)

    def test_empty_history_is_unknown(self):
        self.assertEqual(m.summarize([], self.THRESHOLDS), (m.UNKNOWN, None, 0, 0))

    def test_all_lost_is_down(self):
        self.assertEqual(m.summarize([None, None, None], self.THRESHOLDS),
                         (m.DOWN, None, 3, 3))

    def test_single_spike_is_absorbed_by_median(self):
        self.assertEqual(m.summarize([300, 20, 30], self.THRESHOLDS),
                         (m.GOOD, 30.0, 0, 3))

    def test_one_loss_escalates_to_fair(self):
        state, latency, lost, total = m.summarize([None, 20, 40], self.THRESHOLDS)
        self.assertEqual(state, m.FAIR)
        self.assertEqual(latency, 30.0)
        self.assertEqual((lost, total), (1, 3))

    def test_two_losses_escalate_to_bad(self):
        self.assertEqual(m.summarize([None, None, 20], self.THRESHOLDS)[0], m.BAD)

    def test_loss_never_downgrades_an_already_bad_level(self):
        self.assertEqual(m.summarize([None, 400, 500], self.THRESHOLDS)[0], m.BAD)

    def test_window_keeps_only_the_latest_samples(self):
        history = deque(maxlen=m.SAMPLE_WINDOW)
        for value in (500, 20, 25, 30):
            history.append(value)
        self.assertEqual(m.summarize(history, self.THRESHOLDS), (m.GOOD, 25.0, 0, 3))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self._original_ping = m.ping

    def tearDown(self):
        m.ping = self._original_ping

    def test_returns_latency(self):
        m.ping = lambda *args, **kwargs: 42.5
        self.assertEqual(m.probe("1.1.1.1", 1), 42.5)

    def test_error_false_is_not_zero_latency(self):
        # ping3 用 False 表示解析失败/不可达。老代码把 False 当延迟值，会显示绿色 0
        m.ping = lambda *args, **kwargs: False
        self.assertIsNone(m.probe("1.1.1.1", 1))

    def test_timeout_is_none(self):
        m.ping = lambda *args, **kwargs: None
        self.assertIsNone(m.probe("1.1.1.1", 1))

    def test_non_positive_is_none(self):
        m.ping = lambda *args, **kwargs: 0
        self.assertIsNone(m.probe("1.1.1.1", 1))

    def test_exception_is_swallowed(self):
        def boost(*args, **kwargs):
            raise OSError("raw socket denied")

        m.ping = boost
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(m.probe("1.1.1.1", 1))


class TextTests(unittest.TestCase):
    def test_format_latency(self):
        self.assertEqual(m.format_latency(None), "--")
        self.assertEqual(m.format_latency(45.7), "45")
        self.assertEqual(m.format_latency(1234), "999+")

    def test_widget_text(self):
        self.assertEqual(m.widget_text(m.UNKNOWN, None), "--")
        self.assertEqual(m.widget_text(m.DOWN, None), "×")
        self.assertEqual(m.widget_text(m.GOOD, 45.2), "45")

    def test_widget_text_stays_ascii(self):
        # 挂件字体是 Arial，出现中文会渲染成方块
        for state, latency in ((m.UNKNOWN, None), (m.DOWN, None), (m.GOOD, 45), (m.BAD, 233)):
            with self.subTest(state=state):
                self.assertTrue(all(ord(ch) < 0x100 for ch in m.widget_text(state, latency)))

    def test_icon_text(self):
        self.assertEqual(m.icon_text(m.UNKNOWN, None), "?")
        self.assertEqual(m.icon_text(m.DOWN, None), "×")
        self.assertEqual(m.icon_text(m.BAD, 233.4), "233")

    def test_status_text(self):
        self.assertEqual(m.status_text(m.UNKNOWN, None, 0, 0), "等待测量")
        self.assertEqual(m.status_text(m.DOWN, None, 3, 3), "超时/无响应（丢包 3/3）")
        self.assertEqual(m.status_text(m.GOOD, 45.2, 0, 3), "45 ms")
        self.assertEqual(m.status_text(m.FAIR, 45.2, 1, 3), "45 ms · 丢包 1/3")

    def test_thresholds_text(self):
        self.assertEqual(m.thresholds_text((80.0, 150.0)),
                         "颜色规则: ≤80 绿 / ≤150 黄 / >150 红")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._original_config_file = m.CONFIG_FILE
        self._tmp = tempfile.TemporaryDirectory()
        m.CONFIG_FILE = os.path.join(self._tmp.name, "config.json")

    def tearDown(self):
        m.CONFIG_FILE = self._original_config_file
        self._tmp.cleanup()

    def _load(self, payload=None):
        """写一份配置再读回来，返回 (config, 控制台输出)。"""
        if payload is not None:
            with open(m.CONFIG_FILE, "w", encoding="utf-8") as f:
                if isinstance(payload, str):
                    f.write(payload)
                else:
                    json.dump(payload, f)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            config = m.load_config()
        return config, buffer.getvalue()

    def test_missing_file_uses_defaults(self):
        config, out = self._load()
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})
        self.assertEqual(config["target"], "8.8.8.8")
        self.assertEqual(out, "")

    def test_old_config_without_thresholds(self):
        config, _ = self._load({"target": "baidu.com", "interval": 2})
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})
        self.assertEqual(config["target"], "baidu.com")
        self.assertEqual(config["interval"], 2)

    def test_partial_thresholds(self):
        config, _ = self._load({"thresholds": {"good": 20}})
        self.assertEqual(config["thresholds"], {"good": 20.0, "fair": 150.0})

    def test_custom_thresholds(self):
        config, out = self._load({"thresholds": {"good": 150, "fair": 300}})
        self.assertEqual(config["thresholds"], {"good": 150.0, "fair": 300.0})
        self.assertEqual(out, "")

    def test_numeric_strings_are_accepted(self):
        config, _ = self._load({"thresholds": {"good": "20", "fair": "60"}})
        self.assertEqual(config["thresholds"], {"good": 20.0, "fair": 60.0})

    def test_inverted_thresholds_fall_back(self):
        config, out = self._load({"thresholds": {"good": 300, "fair": 100}})
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})
        self.assertIn("退回默认", out)

    def test_equal_thresholds_fall_back(self):
        config, out = self._load({"thresholds": {"good": 100, "fair": 100}})
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})
        self.assertIn("退回默认", out)

    def test_non_positive_falls_back(self):
        config, out = self._load({"thresholds": {"good": -5, "fair": 0}})
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})
        self.assertIn("正数", out)

    def test_non_numeric_falls_back(self):
        config, out = self._load({"thresholds": {"good": "很快"}})
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})
        self.assertIn("不是数字", out)

    def test_thresholds_not_a_dict(self):
        config, _ = self._load({"thresholds": 5})
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})

    def test_broken_json_uses_defaults(self):
        config, _ = self._load("{ 坏掉的 json")
        self.assertEqual(config["thresholds"], {"good": 80.0, "fair": 150.0})

    def test_save_config_roundtrip(self):
        config, _ = self._load({"target": "1.1.1.1", "thresholds": {"good": 10, "fair": 30}})
        m.save_config(config)
        again, _ = self._load()
        self.assertEqual(again["target"], "1.1.1.1")
        self.assertEqual(again["thresholds"], {"good": 10.0, "fair": 30.0})


class PaletteTests(unittest.TestCase):
    # 任务栏底色的近似值，用来估算挂件文字的对比度
    DARK_BAR = (31, 31, 31)
    LIGHT_BAR = (243, 243, 243)

    def test_every_state_has_a_color(self):
        for theme in ("dark", "light"):
            for state in (m.GOOD, m.FAIR, m.BAD, m.UNKNOWN):
                with self.subTest(theme=theme, state=state):
                    self.assertIn(state, m._WIDGET_COLORS[theme])
        for state in (m.GOOD, m.FAIR, m.BAD, m.UNKNOWN):
            with self.subTest(state=state):
                self.assertIn(state, m._ICON_COLORS)

    def test_widget_text_is_readable_on_both_taskbars(self):
        for theme, bar in (("dark", self.DARK_BAR), ("light", self.LIGHT_BAR)):
            for state, color in m._WIDGET_COLORS[theme].items():
                with self.subTest(theme=theme, state=state):
                    ratio = contrast(color, bar)
                    self.assertGreaterEqual(ratio, 4.5, f"{theme}/{state} 只有 {ratio:.2f}:1")

    def test_icon_text_is_readable(self):
        for state in (m.GOOD, m.FAIR, m.BAD, m.UNKNOWN):
            with self.subTest(state=state):
                background = m.icon_color(state)
                ratio = contrast(m.pick_text_color(background), background)
                self.assertGreaterEqual(ratio, 4.5, f"图标 {state} 只有 {ratio:.2f}:1")

    def test_unknown_stays_neutral_grey(self):
        for color in (m._ICON_COLORS[m.UNKNOWN],
                      m._WIDGET_COLORS["dark"][m.UNKNOWN],
                      m._WIDGET_COLORS["light"][m.UNKNOWN]):
            with self.subTest(color=color):
                self.assertLess(max(color) - min(color), 40, "未知状态应该是无彩色的灰")

    def test_down_renders_as_red_not_grey(self):
        self.assertEqual(m.icon_color(m.DOWN), m._ICON_COLORS[m.BAD])
        original = m.taskbar_is_light
        try:
            for theme, light in (("dark", False), ("light", True)):
                m.taskbar_is_light = lambda light=light: light
                with self.subTest(theme=theme):
                    color = m.widget_color(m.DOWN)
                    self.assertEqual(color, m._WIDGET_COLORS[theme][m.BAD])
                    self.assertNotEqual(color, m._WIDGET_COLORS[theme][m.UNKNOWN])
                    self.assertGreater(color[0], color[1], "无响应应该是红色")
                    self.assertGreater(color[0], color[2], "无响应应该是红色")
        finally:
            m.taskbar_is_light = original

    def test_theme_switch_picks_matching_palette(self):
        original = m.taskbar_is_light
        try:
            m.taskbar_is_light = lambda: True
            self.assertEqual(m.widget_color(m.GOOD), m._WIDGET_COLORS["light"][m.GOOD])
            self.assertEqual(m.widget_color(m.DOWN), m._WIDGET_COLORS["light"][m.BAD])
            m.taskbar_is_light = lambda: False
            self.assertEqual(m.widget_color(m.GOOD), m._WIDGET_COLORS["dark"][m.GOOD])
            self.assertEqual(m.widget_color(m.DOWN), m._WIDGET_COLORS["dark"][m.BAD])
        finally:
            m.taskbar_is_light = original


class SettingsParseTests(unittest.TestCase):
    def test_valid_input(self):
        values, error = m.parse_settings("1.1.1.1", "1", "2", "80", "150", True)
        self.assertIsNone(error)
        self.assertEqual(values, {
            "target": "1.1.1.1",
            "interval": 1,
            "timeout": 2,
            "show_in_taskbar": True,
            "thresholds": {"good": 80.0, "fair": 150.0},
        })

    def test_target_is_trimmed_and_checkbox_kept(self):
        values, error = m.parse_settings("  baidu.com  ", "1", "2", "80", "150", False)
        self.assertIsNone(error)
        self.assertEqual(values["target"], "baidu.com")
        self.assertIs(values["show_in_taskbar"], False)

    def test_empty_target(self):
        values, error = m.parse_settings("   ", "1", "2", "80", "150", True)
        self.assertIsNone(values)
        self.assertIn("不能为空", error)

    def test_non_numeric_field(self):
        for index, args in enumerate((
                ("abc", "2", "80", "150"),
                ("1", "abc", "80", "150"),
                ("1", "2", "abc", "150"),
                ("1", "2", "80", "abc"))):
            with self.subTest(field=index):
                values, error = m.parse_settings("1.1.1.1", *args, True)
                self.assertIsNone(values)
                self.assertIn("必须是数字", error)

    def test_non_finite_is_rejected(self):
        for text in ("nan", "inf", "-inf"):
            with self.subTest(text=text):
                values, error = m.parse_settings("1.1.1.1", text, "2", "80", "150", True)
                self.assertIsNone(values)
                self.assertIn("有限的数字", error)

    def test_interval_lower_bound(self):
        values, error = m.parse_settings("1.1.1.1", "0.1", "2", "80", "150", True)
        self.assertIsNone(values)
        self.assertIn("不能小于", error)
        values, error = m.parse_settings("1.1.1.1", "0.2", "2", "80", "150", True)
        self.assertIsNone(error)
        self.assertEqual(values["interval"], 0.2)

    def test_timeout_must_be_positive(self):
        values, error = m.parse_settings("1.1.1.1", "1", "0", "80", "150", True)
        self.assertIsNone(values)
        self.assertIn("必须大于 0", error)

    def test_threshold_must_be_positive(self):
        values, error = m.parse_settings("1.1.1.1", "1", "2", "0", "150", True)
        self.assertIsNone(values)
        self.assertIn("必须是正数", error)

    def test_green_must_be_below_yellow(self):
        for good, fair in (("150", "80"), ("100", "100")):
            with self.subTest(good=good, fair=fair):
                values, error = m.parse_settings("1.1.1.1", "1", "2", good, fair, True)
                self.assertIsNone(values)
                self.assertIn("小于黄色阈值", error)

    def test_integral_numbers_are_stored_as_int(self):
        values, _ = m.parse_settings("1.1.1.1", "2.0", "3.0", "80.0", "150.0", True)
        self.assertIsInstance(values["interval"], int)
        self.assertIsInstance(values["timeout"], int)
        self.assertEqual((values["interval"], values["timeout"]), (2, 3))


class NumberTextTests(unittest.TestCase):
    def test_integral_values_render_without_decimal(self):
        self.assertEqual(m.number_text(1.0), "1")
        self.assertEqual(m.number_text(80), "80")

    def test_fraction_keeps_decimal(self):
        self.assertEqual(m.number_text(0.5), "0.5")
        self.assertEqual(m.number_text(2.25), "2.25")

    def test_tidy_number(self):
        self.assertEqual(m.tidy_number(1.0), 1)
        self.assertIsInstance(m.tidy_number(1.0), int)
        self.assertEqual(m.tidy_number(0.5), 0.5)


class CenterGeometryTests(unittest.TestCase):
    def test_centers_window(self):
        self.assertEqual(m.center_geometry((1920, 1080), (360, 260)), "360x260+780+410")

    def test_odd_difference_floors(self):
        self.assertEqual(m.center_geometry((1000, 1000), (101, 101)), "101x101+449+449")

    def test_never_goes_negative(self):
        self.assertEqual(m.center_geometry((800, 600), (900, 700)), "900x700+0+0")

    def test_center_position(self):
        self.assertEqual(m.center_position((1920, 1080), (432, 327)), (744, 376))
        self.assertEqual(m.center_position((800, 600), (900, 700)), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
