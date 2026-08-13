import unittest

from bambu_spoolman.gcode.parser import evaluate_gcode, parse_gcode


class GcodeParserTests(unittest.TestCase):
    def test_evaluates_filament_usage_by_layer_and_tool(self):
        gcode = """
; generated fixture
M620 S0A
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


if __name__ == "__main__":
    unittest.main()
