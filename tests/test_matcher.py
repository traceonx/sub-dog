"""Release parsing and candidate scoring for external subtitle matching."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from matcher import parse_release, pick_file, score

MOVIE = 'Interstellar.2014.1080p.BluRay.x264.DTS-RARBG.mkv'


class MatcherTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_release(MOVIE) | {}, {
            'title': 'Interstellar', 'year': 2014, 'season': None, 'episode': None, 'group': 'RARBG',
            'source': 'bluray', 'resolution': '1080p', 'editions': []})
        tv = parse_release('The.Last.of.Us.S01E03.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv')
        self.assertEqual((tv['title'], tv['season'], tv['episode'], tv['group'], tv['source']),
                         ('The Last of Us', 1, 3, 'NTb', 'web'))
        cn = parse_release('黑镜 第二季 第3集.mkv')
        self.assertEqual((cn['title'], cn['season'], cn['episode']), ('黑镜', 2, 3))
        self.assertIsNone(parse_release('星际穿越 (2014) - 1080p.mkv')['group'])

    def test_score_prefers_same_release(self):
        video = parse_release(MOVIE)
        same, _ = score(video, '【圣城家园SCG字幕组】星际穿越/Interstellar.2014.1080p.BluRay.x264.DTS-RARBG', 'SRT', '简繁英', '31287')
        other, _ = score(video, 'Interstellar.2014.2160p.BluRay.x265.10bit.SDR.DTS-HD.MA.5.1-SWTYBLZ', 'ASS', '简英')
        imax, reasons = score(video, '星际穿越.Interstellar.IMAX.2014.Bluray.720p.x264-CMCT.简繁英双语字幕', 'SSA', '简繁英')
        self.assertGreaterEqual(same, 60)
        self.assertLess(other, 60)
        self.assertLess(imax, other)
        self.assertTrue(any('imax' in r for r in reasons))
        # 1920x1080 is a resolution, not a year.
        self.assertIsNotNone(score(video, 'INTERSTELLAR《星际穿越》1920x1080p', 'ASS', '繁')[0])
        self.assertIsNone(score(video, 'Interstellar.1998.DVDRip')[0])

    def test_score_episodes(self):
        tv = parse_release('The.Last.of.Us.S01E03.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv')
        self.assertIsNone(score(tv, 'The.Last.of.Us.S01E04.1080p')[0])
        self.assertIsNone(score(tv, 'The Last of Us S02E03')[0])
        self.assertIn('季包，需从压缩包中挑选', score(tv, '最后生还者 第一季 全9集')[1])

    def test_pick_file_from_season_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for name in ['Show.S01E02.chs.srt', 'Show.S01E03.cht.srt', 'Show.S01E03.chs&eng.ass', 'Show.S01E03.eng.srt']:
                path = Path(tmp) / name
                path.write_text('x')
                files.append(path)
            video = parse_release('Show.S01E03.1080p.WEB-DL-GRP.mkv')
            self.assertEqual(pick_file(files, video).name, 'Show.S01E03.chs&eng.ass')
            self.assertIsNone(pick_file(files, parse_release('Show.S01E09.mkv')))


if __name__ == '__main__':
    unittest.main()
