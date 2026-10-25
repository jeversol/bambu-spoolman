import copy
import unittest

from bambu_spoolman.broker.filament_accounting import (
    mapping_at_position,
    plan_layer_usage,
)
from bambu_spoolman.gcode.parser import FilamentUsage
from bambu_spoolman.settings import EXTERNAL_SPOOL_ID


class FilamentAccountingTests(unittest.TestCase):
    def test_external_holder_groups_usage_without_ams_mapping(self):
        model = {0: {0: 10, 1: 20}}
        for using_ams, mapping in ((False, [0, 1]), (True, []), (True, None)):
            with self.subTest(using_ams=using_ams, mapping=mapping):
                self.assertEqual(
                    plan_layer_usage(model, 0, using_ams=using_ams, mapping=mapping),
                    {(0, EXTERNAL_SPOOL_ID): 10, (1, EXTERNAL_SPOOL_ID): 20},
                )

    def test_missing_usage_or_mapping_defers_but_empty_layer_is_complete(self):
        self.assertIsNone(plan_layer_usage({}, 0, using_ams=False, mapping=None))
        self.assertIsNone(
            plan_layer_usage({0: {0: 10, 2: 20}}, 0, using_ams=True, mapping=[0])
        )
        self.assertEqual(plan_layer_usage({0: {}}, 0, using_ams=True, mapping=[0]), {})

    def test_mapping_history_respects_layer_and_exact_refill_line(self):
        history = [
            {"layer": 2, "mapping": [3]},
            {"layer": 0, "line": 10, "mapping": [1]},
            {"layer": 0, "mapping": [0]},
        ]
        for layer, line, expected in (
            (0, 9, [0]),
            (0, 10, [1]),
            (0, None, [0]),
            (1, None, [1]),
            (2, None, [3]),
        ):
            with self.subTest(layer=layer, line=line):
                self.assertEqual(
                    mapping_at_position([3], history, layer, line), expected
                )

    def test_refill_segments_preserve_total_and_do_not_mutate_inputs(self):
        events = {0: [(2, None, 0, 2.0), (4, None, 0, 3.0), (6, None, 0, 5.0)]}
        model = FilamentUsage({0: {0: 10.0}}, extrusion_events=events)
        history = [
            {"layer": 0, "mapping": [0]},
            {"layer": 0, "line": 4, "mapping": [1]},
        ]
        before = copy.deepcopy((dict(model), model.extrusion_events, history))
        first = plan_layer_usage(
            model, 0, using_ams=True, mapping=[1], mapping_history=history
        )
        second = plan_layer_usage(
            model, 0, using_ams=True, mapping=[1], mapping_history=history
        )
        self.assertEqual(first, {(0, 0): 2.0, (0, 1): 8.0})
        self.assertEqual(second, first)
        self.assertEqual(sum(first.values()), 10.0)
        self.assertEqual((dict(model), model.extrusion_events, history), before)

    def test_line_skip_preserves_prior_extrusion_and_unrelated_objects(self):
        model = FilamentUsage(
            {0: {0: 10.0}},
            object_filaments={0: {7: {0: 5.0}, 8: {0: 5.0}}},
            extrusion_events={0: [(2, 7, 0, 2.0), (4, 7, 0, 3.0), (6, 8, 0, 5.0)]},
        )
        # An object canceled at line 4 retains its earlier 2 mm, even when
        # layer-level skip history also contains that same object.
        self.assertEqual(
            plan_layer_usage(
                model,
                0,
                using_ams=False,
                mapping=None,
                skipped_object_layers={7: 0},
                skipped_object_lines={7: 4},
            ),
            {(0, EXTERNAL_SPOOL_ID): 7.0},
        )
        self.assertEqual(
            plan_layer_usage(
                model, 0, using_ams=False, mapping=None, skipped_object_layers={7: 0}
            ),
            {(0, EXTERNAL_SPOOL_ID): 5.0},
        )

    def test_future_skip_does_not_remove_already_printed_usage(self):
        model = FilamentUsage(
            {0: {0: 5.0}, 1: {0: 6.0}},
            extrusion_events={0: [(2, 7, 0, 5.0)], 1: [(4, 7, 0, 6.0)]},
        )
        context = {"using_ams": False, "mapping": None, "skipped_object_layers": {7: 1}}
        self.assertEqual(
            plan_layer_usage(model, 0, **context), {(0, EXTERNAL_SPOOL_ID): 5.0}
        )
        self.assertEqual(plan_layer_usage(model, 1, **context), {})
