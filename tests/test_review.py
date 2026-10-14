import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pergamon_review as pr  # noqa: E402


def dc(changed, impacted, truncated=False, functions=()):
    items = [{"qn": f, "label": "Module", "file": f, "hop": 1} for f in impacted]
    items += [{"qn": qn, "label": "Function", "file": f, "hop": 2} for qn, f in functions]
    return {"changed_files": changed, "impacted_total": len(items), "truncated": truncated,
            "merge_base": "742816c26d4d263296ce5432378d0ea8cead8dfd", "impacted": items}


class TestPaths(unittest.TestCase):
    def test_test_paths(self):
        for p in ["tests/test_x.py", "pkg/tests/a.py", "web/__tests__/a.js", "a/b_test.go",
                  "src/x.spec.ts", "test_root.py"]:
            self.assertTrue(pr.is_test_path(p), p)

    def test_product_paths_named_testing_are_not_tests(self):
        for p in ["src/app/performance/testing/fixture_cache.py", "src/contest/a.py", "src/app.py"]:
            self.assertFalse(pr.is_test_path(p), p)

    def test_route_method(self):
        self.assertEqual(pr.route_method("__route__POST__/api/v1/tasks"), "POST")
        self.assertEqual(pr.route_method("weird"), "ANY")


class TestAreas(unittest.TestCase):
    def test_grouping_goes_deeper_when_one_folder_dominates(self):
        files = [f"src/app/{m}/f{i}.py" for m in ("api", "core", "jobs") for i in range(3)]
        areas = pr.group_areas(files, 2)
        self.assertEqual(set(areas), {"src/app/api/", "src/app/core/", "src/app/jobs/"})

    def test_grouping_stops_when_spread(self):
        files = ["a/x.py", "b/y.py", "c/z.py"]
        self.assertEqual(set(pr.group_areas(files, 2)), {"a/", "b/", "c/"})

    def test_root_files(self):
        self.assertEqual(pr.area_of("setup.py", 2), "(repo root)")


class TestAnalyse(unittest.TestCase):
    routes = [
        {"path": "/health", "qn": "__route__GET__/health", "handler": "health",
         "handler_qn": "p.api.app.health", "file": "api/app.py", "start_line": "10", "end_line": "12"},
        {"path": "/users", "qn": "__route__GET__/users", "handler": "users",
         "handler_qn": "p.api.app.users", "file": "api/app.py", "start_line": "15", "end_line": "18"},
        {"path": "/other", "qn": "__route__GET__/other", "handler": "o",
         "handler_qn": "p.api.other.o", "file": "api/other.py", "start_line": "1", "end_line": "3"},
    ]

    def paths(self, rs):
        return [r["path"] for r in rs]

    def test_low_impact(self):
        a = pr.analyse(dc(["lib/a.py"], ["lib/b.py"]), self.routes, 2, 10, 50)
        self.assertEqual(a["risk"], "Low")
        self.assertEqual(a["routes"], [])

    def test_only_handlers_that_depend_on_change_are_affected(self):
        a = pr.analyse(dc(["lib/a.py"], ["api/app.py"], functions=[("p.api.app.users", "api/app.py")]),
                       self.routes, 2, 10, 50)
        self.assertEqual(a["risk"], "Medium")
        self.assertEqual(self.paths(a["routes"]), ["/users"])
        self.assertEqual(self.paths(a["nearby_routes"]), ["/health"])

    def test_edited_handler_is_affected_but_neighbours_are_not(self):
        a = pr.analyse(dc(["api/app.py"], []), self.routes, 2, 10, 50, {"api/app.py": [(11, 11)]})
        self.assertEqual(self.paths(a["routes"]), ["/health"])
        self.assertEqual(self.paths(a["nearby_routes"]), ["/users"])

    def test_changed_file_without_line_info_is_conservative(self):
        a = pr.analyse(dc(["api/other.py"], []), self.routes, 2, 10, 50)
        self.assertEqual(self.paths(a["routes"]), ["/other"])

    def test_overlaps(self):
        self.assertTrue(pr.overlaps([(5, 9)], "9", "12"))
        self.assertFalse(pr.overlaps([(5, 8)], "9", "12"))
        self.assertTrue(pr.overlaps([(5, 8)], None, None))

    def test_high_impact_and_tests_split_out(self):
        imp = [f"svc/m{i}.py" for i in range(60)] + ["tests/test_a.py"]
        a = pr.analyse(dc(["core/x.py"], imp), [], 2, 10, 50)
        self.assertEqual(a["risk"], "High")
        self.assertEqual(a["tests"], ["tests/test_a.py"])
        self.assertEqual(len(a["product_files"]), 60)

    def test_changed_file_not_counted_as_dependent(self):
        a = pr.analyse(dc(["core/x.py"], ["core/x.py", "core/y.py"]), [], 2, 10, 50)
        self.assertEqual(a["product_files"], ["core/y.py"])


class TestRender(unittest.TestCase):
    def render(self, a, **kw):
        args = dict(depth=2, engine_version="v0.11.0", server="https://github.com",
                    repo="o/r", sha="abc", max_rows=2)
        args.update(kw)
        return pr.render(a, **args)

    def test_marker_and_links(self):
        a = pr.analyse(dc(["a/x.py"], ["b/y.py"]), [], 2, 10, 50)
        md = self.render(a)
        self.assertTrue(md.startswith(pr.MARKER))
        self.assertIn("https://github.com/o/r/blob/abc/b/y.py", md)
        self.assertIn("**1 other file** depend", md.replace("**0 functions** and ", ""))

    def test_row_limit_and_pipe_escaping(self):
        routes = [{"path": f"/r{i}|x", "qn": "__route__GET__/r", "handler": "h", "file": "a/x.py"}
                  for i in range(5)]
        md = self.render(pr.analyse(dc(["a/x.py"], []), routes, 2, 10, 50))
        self.assertIn("…and 3 more.", md)
        self.assertIn("/r0\\|x", md)

    def test_code_spans_survive_backticks_and_pipes(self):
        self.assertEqual(pr.code("a|b"), "`a\\|b`")
        self.assertEqual(pr.code("a`b"), "`` a`b ``")

    def test_symbol_count_in_summary(self):
        a = pr.analyse(dc(["a/x.py"], [], functions=[("p.a.x.f", "a/x.py"), ("p.a.x.g", "a/x.py")]), [], 2, 10, 50)
        self.assertIn("**2 functions** and **0 other files** depend", self.render(a))

    def test_no_changes_message(self):
        md = self.render(pr.analyse(dc([], []), [], 2, 10, 50))
        self.assertIn("No indexed source files changed", md)

    def test_truncation_note(self):
        md = self.render(pr.analyse(dc(["a.py"], [], truncated=True), [], 2, 10, 50))
        self.assertIn("lower bound", md)


class TestChangedLines(unittest.TestCase):
    def test_parses_head_side_ranges(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True, text=True)
            run("git", "init", "-q", "-b", "main")
            with open(os.path.join(d, "a.py"), "w") as fh:
                fh.write("".join(f"line{i}\n" for i in range(1, 11)))
            run("git", "add", "-A")
            run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
            base = run("git", "rev-parse", "HEAD").stdout.strip()
            lines = open(os.path.join(d, "a.py")).read().splitlines()
            lines[2] = "changed3"
            lines.insert(7, "new")
            with open(os.path.join(d, "a.py"), "w") as fh:
                fh.write("\n".join(lines) + "\n")
            cwd = os.getcwd()
            try:
                os.chdir(d)
                ranges = pr.changed_lines(base)
            finally:
                os.chdir(cwd)
            self.assertEqual(ranges, {"a.py": [(3, 3), (8, 8)]})


if __name__ == "__main__":
    unittest.main()
