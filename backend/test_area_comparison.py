import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from .area_comparison import AreaComparison, BUDGETS, HOLDOUTS, load_area_catalog, load_area_comparison, verify_city_support
from .prepare_area_comparison import cell_areas
from .climate import SCENARIOS, load_prepared_climate
from .datasets import Grid, read_dataset, DEFAULT_MANIFEST
from .main import app
from .sample import FULL_RELEASE_ID, MaxSSSProfile, load_prepared_sample, prepare_sample


class AreaComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.ids = [f"PROD{i:04}" for i in range(1, 40)]
        template = read_dataset(DEFAULT_MANIFEST)
        dataset = template.model_copy(update={
            "release_id": FULL_RELEASE_ID,
            "species": [template.species[0].model_copy(update={"id": key}) for key in self.ids],
        })
        self.grid = Grid(bounds=(120, 30, 121, 30.1), height=1, width=10, row0=100, col0=200)
        baseline = np.full((39, 10), 0.25, dtype=np.float32)
        baseline[0, :3] = [0.75, 0.75, 0.875]
        self.sample = prepare_sample(dataset, self.grid, baseline[:, None, :], "fixture",
                                     MaxSSSProfile(dataVersion="fixture", thresholdTable="t.csv",
                                                   thresholdTableSha256="0" * 64, archiveCellCount=10,
                                                   thresholds=dict.fromkeys(self.ids, 0.5), demoOnlyCells=[]))
        future = np.broadcast_to(baseline, (6, 39, 10)).copy()
        future[:, 0, :6] = [0.875, 0.875, 0.75, 0.625, 0.75, 0.75]
        future[:, 1, 0] = 0.875
        future[-1, 0, [1, 5]] = 0.25
        self.climate = {name: (future[i, :, None, :], {}) for i, name in enumerate(SCENARIOS)}
        self.meta = dict(schema=1, cityId="330400", releaseVersion="fixture", speciesIds=self.ids,
                         scenarios=list(SCENARIOS), grid=self.grid.model_dump(mode="json"), provenance={})
        self.arrays = dict(indices=np.arange(10), baseline=baseline, future=future,
                           thresholds=self.sample.thresholds, areas=np.ones(10))

    def load(self):
        np.savez(self.directory / "area-330400.npz", meta=json.dumps(self.meta), **self.arrays)
        # The ranking fixtures below intentionally use ten synthetic cells.
        with patch("backend.area_comparison.verify_city_support"):
            return load_area_comparison(self.directory, self.sample, self.climate)

    def test_routes_registered(self):
        self.assertIn("/api/area-comparisons/{city_id}", app.openapi()["paths"])

    def test_percentiles_ties_and_held_out_evaluation(self):
        area = self.load()
        run = area.comparison(10, "ssp585")
        self.assertEqual(run["allocations"]["site"], [1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(run["eligibleGainSpecies"], 2)
        self.assertEqual(run["summary"]["site"], {"gainPct": 50, "persistentPct": 50, "gainSpecies": 1})
        self.assertEqual(area.habitat(self.ids[0], "ssp585"), {"persistent": [0, 2], "gain": [3, 4]})
        self.assertEqual(run["species"][self.ids[0]]["site"], {"persistentKm2": 1, "gainKm2": 0})
        self.assertEqual(area.cell(100, 200, self.ids[0], "ssp585")["drivers"],
                         [{"speciesId": self.ids[0], "state": "persistent"}, {"speciesId": self.ids[1], "state": "gain"}])
        self.assertAlmostEqual(area.cell(100, 203, self.ids[0], "ssp585")["scores"]["site"], 1 / 3)
        self.assertEqual(area.cell(100, 206, self.ids[1], "ssp585")["drivers"], [])
        self.assertEqual(area.comparison(5, "ssp585")["allocations"]["site"][0], 0.5)

    def test_fp_boundary_does_not_select_a_sliver(self):
        self.arrays["areas"] = np.array([0.1, 0.2, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.6000000000000005])
        result = self.load().comparison(10, "ssp585")
        self.assertEqual(result["allocations"]["site"], [0.1, 0.2, 0, 0, 0, 0, 0, 0, 0, 0])

    def test_package_must_align_to_verified_predictions(self):
        self.meta["grid"]["bounds"][0] -= 0.01
        with self.assertRaises(ValueError):
            self.load()
        self.meta["grid"] = self.grid.model_dump(mode="json")
        self.arrays["thresholds"] = self.sample.thresholds + 0.000001
        with self.assertRaises(ValueError):
            self.load()
        self.arrays["thresholds"] = self.sample.thresholds
        self.arrays["baseline"] = self.arrays["baseline"].copy()
        self.arrays["baseline"][3, 3] += 0.01
        with self.assertRaises(ValueError):
            self.load()

    def test_release_without_city_package_still_loads(self):
        self.assertIsNone(load_area_comparison(self.directory, self.sample, self.climate))

    def make_city_index(self):
        cities = []
        # Disjoint native windows outside the original sample, with different species evidence.
        for n, city_id in enumerate(("111111", "222222"), start=1):
            south = 31 - n
            grid = Grid(bounds=(120 + n, south, 121 + n, south + 0.1), height=1, width=10,
                        row0=self.grid.row0 + (n - 1) * 10, col0=self.grid.col0 + n * 10)
            meta = dict(self.meta, cityId=city_id, grid=grid.model_dump(mode="json"))
            ix = self.arrays["indices"]
            areas = cell_areas(grid, ix)
            future = self.arrays["future"] if n == 1 else np.zeros_like(self.arrays["future"])
            path = self.directory / f"area-{city_id}.npz"
            np.savez(path, meta=json.dumps(meta), **dict(self.arrays, future=future, areas=areas))
            calculated = AreaComparison(meta, grid, ix, areas, self.arrays["baseline"].astype(float),
                                        future.astype(float), self.sample.thresholds, "fixture")
            results = {h: {str(b): {k: calculated.comparison(b, h)[k] for k in ("summary", "eligibleGainSpecies")}
                           for b in BUDGETS} for h in HOLDOUTS}
            coords = np.column_stack((grid.row0 + ix // grid.width, grid.col0 + ix % grid.width)).astype("<i4")
            cities.append(dict(cityId=city_id, nameZh=city_id, nameEn=city_id, cellCount=len(ix),
                               areaKm2=float(areas.sum()), file=path.name, sha256=sha256(path.read_bytes()).hexdigest(),
                               supportSha256=sha256(coords.tobytes()).hexdigest(), results=results))
        index = dict(schema=1, releaseVersion=self.sample.data_version,
                     thresholdTableSha256=self.sample.maxsss.thresholdTableSha256, cities=cities)
        (self.directory / "area-cities.json").write_text(json.dumps(index), encoding="utf-8")
        return index

    def test_index_is_lazy_and_drilldown_is_city_specific(self):
        self.make_city_index()
        with patch("backend.area_comparison.read_city", side_effect=AssertionError("unexpected map read")):
            collection = load_area_catalog(self.directory, self.sample, self.climate)
            rows = collection.overview(10, "ssp585")["cities"]
        self.assertEqual([r["eligibleGainSpecies"] for r in rows], [2, 0])
        self.assertNotEqual(rows[0]["budgetKm2"], rows[1]["budgetKm2"])
        self.assertIsNone(rows[1]["summary"]["site"]["gainPct"])
        for row in rows:
            run = collection.get(row["cityId"]).comparison(10, "ssp585")
            self.assertEqual(run["cityId"], row["cityId"])
            self.assertEqual(run["summary"], row["summary"])
            for allocated in run["allocations"].values():
                self.assertAlmostEqual(sum(allocated), row["budgetKm2"])
        with self.assertRaises(KeyError):
            collection.get("999999")

    def test_changed_city_bundle_fails_before_map_display(self):
        index = self.make_city_index()
        collection = load_area_catalog(self.directory, self.sample, self.climate)
        (self.directory / index["cities"][0]["file"]).write_bytes(b"fixture changed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            collection.get("111111")
        self.assertEqual(collection.get("222222").comparison(5, "ssp126")["cityId"], "222222")

    def test_directory_footprints_use_verified_cells_without_loading_predictions(self):
        index = self.make_city_index()
        collection = load_area_catalog(self.directory, self.sample, self.climate)
        with patch("backend.area_comparison.read_city", side_effect=AssertionError("unexpected prediction read")):
            features = json.loads(collection.footprint_geojson())["features"]
        self.assertEqual([f["properties"]["cityId"] for f in features], ["111111", "222222"])
        self.assertEqual(set(map(tuple, features[0]["geometry"]["coordinates"][0][0])),
                         {(121, 30), (122, 30), (121, 30.1), (122, 30.1)})
        index["cities"][1]["supportSha256"] = "0" * 64
        (self.directory / "area-cities.json").write_text(json.dumps(index), encoding="utf-8")
        invalid = load_area_catalog(self.directory, self.sample, self.climate)
        with self.assertRaisesRegex(ValueError, "municipal support"):
            invalid.footprint_geojson()
        self.assertIsNone(invalid.outlines)


RELEASE_DIR = Path(__file__).resolve().parent.parent / "data/jiaxing-39"


@unittest.skipUnless((RELEASE_DIR / "area-330400.npz").is_file(), "local Jiaxing municipal package unavailable")
class JiaxingComparisonTests(unittest.TestCase):
    def test_rejects_changed_support_and_redistributed_area(self):
        with np.load(RELEASE_DIR / "area-330400.npz", allow_pickle=False) as bundle:
            grid = Grid.model_validate(json.loads(bundle["meta"].item())["grid"])
            indices, areas = bundle["indices"], bundle["areas"]
        with self.assertRaisesRegex(ValueError, "municipal support"):
            verify_city_support(grid, indices[1:], areas[1:])
        areas[0] += 0.01
        areas[-1] -= 0.01
        with self.assertRaisesRegex(ValueError, "WGS84"):
            verify_city_support(grid, indices, areas)

    def test_city_budgets(self):
        sample = load_prepared_sample(RELEASE_DIR)
        area = load_area_comparison(RELEASE_DIR, sample, load_prepared_climate(RELEASE_DIR, sample))
        for holdout in ("ssp126", "ssp245", "ssp585"):
            for budget in (5, 10, 20):
                run = area.comparison(budget, holdout)
                cell_areas = np.array(run["areas"])
                for selected in run["allocations"].values():
                    allocated = np.array(selected)
                    self.assertAlmostEqual(allocated.sum(), cell_areas.sum() * budget / 100, places=8)
                    self.assertTrue(((allocated >= 0) & (allocated <= cell_areas)).all())
                    self.assertLessEqual(np.count_nonzero((allocated > 0) & (allocated < cell_areas)), 1)

    @unittest.skipUnless((RELEASE_DIR.parent / "area-comparison-reference.json").is_file(), "local reference unavailable")
    def test_archived_reference_results(self):
        expected = json.loads((RELEASE_DIR.parent / "area-comparison-reference.json").read_bytes())
        sample = load_prepared_sample(RELEASE_DIR)
        area = load_area_comparison(RELEASE_DIR, sample, load_prepared_climate(RELEASE_DIR, sample))
        run = area.comparison(expected["budget"], expected["holdout"])
        self.assertEqual(run["eligibleGainSpecies"], expected["eligibleGainSpecies"])
        for rule, (persistent, gain, species) in expected["strategies"].items():
            self.assertAlmostEqual(run["summary"][rule]["persistentPct"], persistent, places=2)
            self.assertAlmostEqual(run["summary"][rule]["gainPct"], gain, places=2)
            self.assertEqual(run["summary"][rule]["gainSpecies"], species)
