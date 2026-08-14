import unittest

from bambu_spoolman.gcode.parser import evaluate_gcode, parse_gcode


class GcodeParserTests(unittest.TestCase):
    def test_evaluates_filament_usage_by_layer_and_tool(self):
        gcode = """
; generated fixture
M620 S0A
M83
G1 X10 E1.5
M73 L1
G1 E2
M620 S1
G2 E3
M620 S255
G1 E99
M73 L2
M620 S1
G3 E4
"""

        usage = evaluate_gcode(gcode)

        self.assertEqual(
            usage,
            {
                0: {0: 1.5},
                1: {0: 2.0, 1: 3.0},
                2: {1: 4.0},
            },
        )

    def test_parse_gcode_consumes_input_lazily(self):
        lines_read = []

        def lines():
            for line in ("G1 E1\n", "G1 E2\n"):
                lines_read.append(line)
                yield line

        operations = parse_gcode(lines())

        self.assertEqual(lines_read, [])
        self.assertEqual(next(operations).params["E"], "1")
        self.assertEqual(lines_read, ["G1 E1\n"])

    def test_retractions_are_not_counted_as_negative_usage(self):
        gcode = """
M620 S0
M83
G1 E10
G1 E-2
M73 L1
G1 E2
G1 E5
"""

        usage = evaluate_gcode(gcode)

        self.assertEqual(usage, {0: {0: 10.0}, 1: {0: 5.0}})

    def test_supports_absolute_extrusion(self):
        gcode = """
M620 S0
M82
G92 E0
G1 E10
G1 E8
G1 E13
"""

        usage = evaluate_gcode(gcode)

        self.assertEqual(usage, {0: {0: 13.0}})

    def test_reconciles_layers_to_slicer_filament_totals(self):
        gcode = """
; total filament length [mm] : 30.00
M620 S5
M83
G1 E10
M73 L1
G1 E10
"""

        usage = evaluate_gcode(gcode)

        self.assertEqual(usage, {0: {5: 15.0}, 1: {5: 15.0}})

    def test_ignores_non_finite_slicer_totals(self):
        gcode = """
; total filament length [mm] : nan
M620 S0
M83
G1 E10
"""

        usage = evaluate_gcode(gcode)

        self.assertEqual(usage, {0: {0: 10.0}})

    def test_tracks_object_usage_without_including_shared_extrusion(self):
        gcode = """
M620 S0
M83
G1 E2
; start printing object, unique label id: 7
G1 E3
; stop printing object, unique label id: 7
; start printing object, unique label id: 8
G1 E5
; stop printing object, unique label id: 8
"""

        usage = evaluate_gcode(gcode)

        self.assertEqual(usage, {0: {0: 10.0}})
        self.assertEqual(usage.for_layer(0, {7}), {0: 7.0})
        self.assertEqual(usage.for_layer(0, {7, 8}), {0: 2.0})

    def test_line_position_preserves_object_usage_before_skip(self):
        gcode = """M620 S0
M83
; start printing object, unique label id: 7
G1 E3
G1 X1
G1 E5
; stop printing object, unique label id: 7
"""

        usage = evaluate_gcode(gcode)

        self.assertEqual(
            usage.for_layer(0, skipped_object_lines={7: 5}),
            {0: 3.0},
        )


if __name__ == "__main__":
    unittest.main()
