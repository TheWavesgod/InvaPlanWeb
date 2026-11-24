import unittest
from dataclasses import replace

import numpy as np

from .datasets import DEFAULT_MANIFEST, Grid, read_dataset
from .policy import build_run, evaluate, shortlist_summary
from .sample import prepare_sample


class SelectionTests(unittest.TestCase):
    def setUp(self):
        dataset = read_dataset(DEFAULT_MANIFEST)
        self.ids = [s.id for s in dataset.species[:2]]
        # The last cell has a lower positive score, cell 3 is incomplete.
        values = np.ones((len(dataset.species), 1, 6), dtype=np.float32)
        values[0, 0, 3] = np.nan
        values[1, 0, 5] = 0
        grid = Grid(bounds=(0, 0, 6, 1), width=6, height=1, row0=0, col0=0)
        self.sample = replace(prepare_sample(dataset, grid, values, "fixture"),
                              thresholds=np.full(len(dataset.species), 0.5))

    def plan(self, k, excluded=(), **options):
        return build_run(self.sample, evaluate(self.sample, self.ids, list(excluded)), {"k": k, **options})

    def test_spread_breaks_ties_without_changing_scores(self):
        original = self.plan(5)
        dispersed = self.plan(5, tieBreak="spatial_spread")
        self.assertEqual(original["scores"], dispersed["scores"])
        self.assertEqual([s["col"] for s in original["recommendations"]], [0, 1, 2, 4, 5])
        self.assertEqual([s["col"] for s in dispersed["recommendations"]], [0, 4, 2, 1, 5])
        self.assertGreater(dispersed["recommendations"][1]["nearestEarlierKm"], 400)

    def test_exclusions_and_short_plans(self):
        excluded = ["r00000c00000", "r00000c00001", "r00000c00002", "r00000c00004"]
        single = self.plan(10, excluded, tieBreak="spatial_spread")
        self.assertEqual([s["col"] for s in single["recommendations"]], [5])
        self.assertIsNone(single["medianNearestKm"])
        empty = self.plan(10, [*excluded, "r00000c00005"], tieBreak="spatial_spread")
        self.assertEqual(empty["recommendations"], [])
        self.assertIsNone(empty["medianNearestKm"])

    def test_shortlist_metrics_are_recomputed_for_the_edited_cells(self):
        evidence = evaluate(self.sample, self.ids, [])
        plan = self.plan(2)
        original = shortlist_summary(self.sample, evidence, [s["id"] for s in plan["recommendations"]])
        self.assertEqual(original["medianNearestKm"], plan["medianNearestKm"])
        self.assertEqual(original["speciesCellCounts"], dict.fromkeys(self.ids, 2))
        edited = shortlist_summary(self.sample, evidence, ["r00000c00005"])
        self.assertEqual(edited["coveredSpeciesIds"], [self.ids[0]])
        self.assertEqual(edited["speciesCellCounts"], {self.ids[0]: 1, self.ids[1]: 0})
        self.assertIsNone(edited["demoDerivedCellCount"])
        empty = shortlist_summary(self.sample, evidence, [])
        self.assertEqual(empty["coveredSpeciesIds"], [])
        self.assertEqual(empty["speciesCellCounts"], dict.fromkeys(self.ids, 0))
        self.assertIsNone(empty["medianNearestKm"])
