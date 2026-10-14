import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pergamon_review as pr  # noqa: E402


def dc(changed, impacted, truncated=False):
    return {"changed_files": changed, "impacted_total": len(impacted), "truncated": truncated,
            "merge_base": "742816c26d4d263296ce5432378d0ea8cead8dfd",
            "impacted": [{"qn": f, "label": "Module", "file": f, "hop": 1} for f in impacted]}


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
    routes = [{"path": "/health", "qn": "__route__GET__/health", "handler": "health", "file": "api/app.py"},
              {"path": "/other", "qn": "__route__GET__/other", "handler": "o", "file": "api/other.py"}]

    def test_low_impact(self):
        a = pr.analyse(dc(["lib/a.py"], ["lib/b.py"]), self.routes, 2, 10, 50)
        self.assertEqual(a["risk"], "Low")
        self.assertEqual(a["routes"], [])

    def test_routes_raise_risk_and_only_touched_files_count(self):
        a = pr.analyse(dc(["lib/a.py"], ["api/app.py"]), self.routes, 2, 10, 50)
        self.assertEqual(a["risk"], "Medium")
        self.assertEqual([r["path"] for r in a["routes"]], ["/health"])

    def test_route_in_changed_file_counts(self):
        a = pr.analyse(dc(["api/other.py"], []), self.routes, 2, 10, 50)
        self.assertEqual([r["path"] for r in a["routes"]], ["/other"])

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
        self.assertIn("**1 other file** depend", md)

    def test_row_limit_and_pipe_escaping(self):
        routes = [{"path": f"/r{i}|x", "qn": "__route__GET__/r", "handler": "h", "file": "a/x.py"}
                  for i in range(5)]
        md = self.render(pr.analyse(dc(["a/x.py"], []), routes, 2, 10, 50))
        self.assertIn("…and 3 more.", md)
        self.assertIn("/r0\\|x", md)

    def test_no_changes_message(self):
        md = self.render(pr.analyse(dc([], []), [], 2, 10, 50))
        self.assertIn("No indexed source files changed", md)

    def test_truncation_note(self):
        md = self.render(pr.analyse(dc(["a.py"], [], truncated=True), [], 2, 10, 50))
        self.assertIn("lower bound", md)


if __name__ == "__main__":
    unittest.main()
