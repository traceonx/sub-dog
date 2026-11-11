"""Hard-link creation and delayed orphan cleanup, on a real filesystem."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linker import Linker, LinkError


class LinkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib, self.media, self.config = root / 'subtitles', root / 'media', root / 'config'
        for d in (self.lib, self.media, self.config):
            d.mkdir()
        (self.lib / 'job1').mkdir()
        self.sub = self.lib / 'job1' / 'Interstellar.chs.srt'
        self.sub.write_text('1\n00:00:01,000 --> 00:00:02,000\nHi\n')
        self.movie_dir = self.media / '星际穿越 (2014)'
        self.movie_dir.mkdir()
        (self.movie_dir / 'Interstellar.2014.1080p.mkv').write_bytes(b'video')
        os.environ.pop('AUTO_CLEAN', None)
        os.environ.pop('CLEAN_DELAY', None)
        self.linker = Linker(self.lib, self.media, self.config / 'links.json')

    def tearDown(self):
        self.tmp.cleanup()

    def link(self):
        return self.linker.link('job1/Interstellar.chs.srt', '星际穿越 (2014)/Interstellar.2014.1080p.mkv', 'zh-CN')

    def test_link_creates_named_hardlink(self):
        result = self.link()
        target = self.movie_dir / 'Interstellar.2014.1080p.zh-CN.srt'
        self.assertEqual(result['target'], '星际穿越 (2014)/Interstellar.2014.1080p.zh-CN.srt')
        self.assertTrue(os.path.samefile(target, self.sub))
        self.assertEqual(self.sub.stat().st_nlink, 2)
        self.assertTrue(self.link()['existing'])  # idempotent

    def test_link_rejects_bad_input(self):
        with self.assertRaises(LinkError):
            self.linker.link('job1/Interstellar.chs.srt', '../outside.mkv', 'zh-CN')
        with self.assertRaises(LinkError):
            self.linker.link('../config/links.json', '星际穿越 (2014)/Interstellar.2014.1080p.mkv', 'zh-CN')
        with self.assertRaises(LinkError):
            self.linker.link('job1/Interstellar.chs.srt', '星际穿越 (2014)/Interstellar.2014.1080p.mkv', 'xx')
        (self.movie_dir / 'Interstellar.2014.1080p.zh-CN.srt').write_text('other')
        with self.assertRaises(LinkError) as ctx:
            self.link()
        self.assertEqual(ctx.exception.status, 409)

    def test_orphan_deleted_after_delay(self):
        self.link()
        self.linker.scan(now=1000)
        for p in self.movie_dir.iterdir():  # user deletes the media folder
            p.unlink()
        self.movie_dir.rmdir()
        self.linker.scan(now=1000)
        self.assertEqual(self.linker.status('job1/Interstellar.chs.srt', now=1010)['delete_in'], 20)
        self.assertEqual(self.linker.scan(now=1029), [])
        self.assertTrue(self.sub.exists())
        self.assertEqual(self.linker.scan(now=1030), ['job1/Interstellar.chs.srt'])
        self.assertFalse(self.sub.exists())
        self.assertFalse((self.lib / 'job1').exists())  # empty task folder removed
        self.assertEqual(self.linker.public_settings()['history'][0]['path'], 'job1/Interstellar.chs.srt')

    def test_restored_link_cancels_deletion(self):
        self.link()
        target = self.movie_dir / 'Interstellar.2014.1080p.zh-CN.srt'
        target.unlink()
        self.linker.scan(now=0)
        os.link(self.sub, target)  # restored within the grace period
        self.linker.scan(now=10)
        self.linker.scan(now=100)
        self.assertTrue(self.sub.exists())
        self.assertIsNone(self.linker.status('job1/Interstellar.chs.srt')['delete_in'])

    def test_never_linked_files_untouched(self):
        self.linker.scan(now=0)
        self.linker.scan(now=10_000)
        self.assertTrue(self.sub.exists())

    def test_manual_hardlink_is_adopted(self):
        os.link(self.sub, self.movie_dir / 'manual.zh.srt')
        self.linker.scan(now=0)
        (self.movie_dir / 'manual.zh.srt').unlink()
        self.linker.scan(now=1)
        self.linker.scan(now=40)
        self.assertFalse(self.sub.exists())

    def test_keep_and_disabled(self):
        self.link()
        (self.movie_dir / 'Interstellar.2014.1080p.zh-CN.srt').unlink()
        self.linker.scan(now=0)
        self.linker.keep('job1/Interstellar.chs.srt')
        self.linker.scan(now=100)
        self.assertTrue(self.sub.exists())
        # Disabled: a newly orphaned file is not counted down.
        other = self.lib / 'job1' / 'b.ass'
        other.write_text('x')
        os.link(other, self.movie_dir / 'b.zh.ass')
        self.linker.update_settings(False, 30)
        self.linker.scan(now=200)
        (self.movie_dir / 'b.zh.ass').unlink()
        self.linker.scan(now=201)
        self.linker.scan(now=500)
        self.assertTrue(other.exists())

    def test_replaced_file_not_deleted(self):
        self.link()
        self.linker.scan(now=0)
        (self.movie_dir / 'Interstellar.2014.1080p.zh-CN.srt').unlink()
        self.sub.unlink()
        self.sub.write_text('new download, never linked')
        self.linker.scan(now=1)
        self.linker.scan(now=100)
        self.assertTrue(self.sub.exists())

    def test_state_persists(self):
        self.link()
        self.linker.update_settings(True, 60)
        again = Linker(self.lib, self.media, self.config / 'links.json')
        self.assertEqual(again.settings['delay'], 60)
        self.assertIn('job1/Interstellar.chs.srt', again.entries)

    def test_browse(self):
        self.assertEqual(self.linker.browse('')['dirs'], ['星际穿越 (2014)'])
        self.assertEqual(self.linker.browse('星际穿越 (2014)')['videos'], ['Interstellar.2014.1080p.mkv'])
        with self.assertRaises(LinkError):
            self.linker.browse('../')


if __name__ == '__main__':
    unittest.main()
