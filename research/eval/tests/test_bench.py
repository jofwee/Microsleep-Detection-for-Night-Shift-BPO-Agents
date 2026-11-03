from evalkit.bench import BenchResult, summarize


def test_summary_of_frame_times():
    r = summarize("x", [20.0] * 99 + [120.0], wall_s=3.0, cpu=[100.0, 140.0], rss_mb=[500.0, 620.0])
    assert r.frames == 100 and r.mean_ms == 21.0 and r.median_ms == 20.0 and r.max_ms == 120.0
    assert r.fps_sustained == 100 / 3.0 and r.meets_target  # 33 per second: above the 15 target
    assert r.cpu_percent_mean == 120.0 and r.memory_mb_peak == 620.0


def test_a_slow_pipeline_is_reported_as_below_the_target_and_nothing_crashes_on_empty_input():
    slow = summarize("slow", [100.0] * 10, wall_s=1.0)
    assert slow.fps_sustained == 10.0 and not slow.meets_target and slow.cpu_percent_mean is None
    empty = summarize("none", [], wall_s=0.0)
    assert isinstance(empty, BenchResult) and empty.frames == 0 and empty.fps_sustained == 0.0 and not empty.meets_target
