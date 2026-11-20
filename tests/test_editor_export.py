import array
import csv
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from factory import media
from factory.common import read, write
from factory.editor import export_edit

try:
    import opentimelineio as otio
except ImportError:
    otio = None


class EditorExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.job = cls.root/'job'
        take = cls.job/'takes'/'selected'
        take.mkdir(parents=True)
        cls.cut = take/'cut.mp4'
        media.ff('-f', 'lavfi', '-i', 'color=blue:s=160x96:r=24', '-f', 'lavfi', '-i',
                 'sine=f=650:r=48000', '-t', '1', '-c:v', 'libx264', '-c:a', 'aac', '-ac', '2', cls.cut)
        media.ff('-f', 'lavfi', '-i', 'color=blue:s=160x96:r=24', '-frames:v', '25', take/'source.mp4')
        write(take/'source-ready.json', {'file': 'source.mp4'})
        cls.release = cls.job/'deliveries'/'published'
        cls.release.mkdir(parents=True)
        cls.timeline = [dict(shot=i, code=f'C{i:03}', file='takes\\selected\\cut.mp4', seconds=1,
                             frames=24, sound='silence' if i == 3 else 'room tone',
                             dialogue='再见' if i == 2 else '', beat=f'拍摄{i}') for i in range(1, 4)]
        write(cls.release/'timeline.json', cls.timeline)
        write(cls.job/'timeline.json', [])
        (cls.job/'score.wav').write_bytes(b'outdated build')
        media.ff('-f', 'lavfi', '-i', 'sine=f=2000:r=48000', '-t', '3', cls.release/'score.wav')
        write(cls.release/'acceptance.json', {'audio_mix': {'gain_db': -1.25, 'score': True}})
        cls.store = SimpleNamespace(root=cls.root, job=lambda jid: {'state': 'done'},
                                    delivery_dir=lambda jid: cls.release)
        cls.package = cls.root/'剪辑 with spaces'
        cls.edit = export_edit(cls.store, 'job', cls.package)

    def test_portable_multitrack_and_actual_source_handles(self):
        edit = read(self.edit)
        tracks = edit['tracks']['children']
        self.assertEqual([t['kind'] for t in tracks], ['Video', 'Audio', 'Audio'])
        self.assertEqual([t['name'] for t in tracks], ['Picture', 'Foreground', 'Music'])
        self.assertEqual([c['source_range']['start_time']['value'] for c in tracks[1]['children']], [0,24,48])
        self.assertEqual(tracks[2]['children'][0]['source_range']['duration']['value'], 72)
        with tempfile.TemporaryDirectory() as dest:
            relocated = Path(dest)/'另一个盘'
            shutil.copytree(self.package, relocated)
            for track in tracks:
                for clip in track['children']:
                    self.assertEqual(clip['source_range']['duration']['rate'], 24)
                    for ref in clip['media_references'].values():
                        self.assertFalse(Path(ref['target_url']).is_absolute())
                        self.assertTrue((relocated/ref['target_url']).is_file())
        clip = tracks[0]['children'][0]
        meta = clip['metadata']['film_factory']
        self.assertEqual((meta['picture_handle_out_frames'], meta['original_handle_out_frames']), (0, 1))
        self.assertEqual((self.package/meta['original']).read_bytes(), (self.cut.parent/'source.mp4').read_bytes())
        full = media.probe(self.package/clip['media_references']['DEFAULT_MEDIA']['target_url'])
        self.assertFalse(media.stream(full,'audio'))
        proxy = media.probe(self.package/meta['proxy'], count=True)
        v = media.stream(proxy, 'video')
        self.assertEqual((v['width'], v['height'], int(v['nb_read_frames'])), (1280, 720, 24))
        self.assertTrue(media.stream(proxy, 'audio'))
        with (self.package/'timeline.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([(r['in_frame'], r['out_frame_exclusive']) for r in rows], [('0','24'),('24','48'),('48','72')])

    def test_music_bed_ducks_and_mutes_while_foreground_is_separate(self):
        def rms(name, t):
            data = media.ff('-ss', str(t), '-i', self.package/name, '-t', '0.2', '-ac', '1', '-f', 'f32le', '-')
            values = array.array('f', data)
            return math.sqrt(sum(v*v for v in values)/len(values))
        music = rms('music-bed.wav', 0.45)
        self.assertGreater(music, 0.001)
        self.assertLess(rms('music-bed.wav', 1.45), music * .65)
        self.assertEqual(rms('music-bed.wav', 2.45), 0)
        self.assertGreater(rms('foreground.wav', 1.45), .001)
        self.assertEqual(rms('foreground.wav', 2.45), 0)
        self.assertEqual((self.package/'score.wav').read_bytes(), (self.release/'score.wav').read_bytes())

    @unittest.skipUnless(otio, 'Install opentimelineio to run the official reader round trip')
    def test_official_otio_reader_roundtrip(self):
        timeline = otio.adapters.read_from_file(str(self.edit))
        self.assertEqual(timeline.duration().to_frames(24), 72)
        self.assertEqual(len(timeline.audio_tracks()), 2)
        picture = timeline.video_tracks()[0]
        self.assertEqual(picture[1].range_in_parent().start_time.to_frames(24),24)
        self.assertEqual(picture[1].media_reference.target_url,'picture/S02-C002-picture.mp4')
        self.assertEqual(picture[1].media_references()['PROXY'].target_url,'S02-C002.mp4')
        restored = otio.adapters.read_from_string(otio.adapters.write_to_string(timeline))
        self.assertEqual(restored.duration(), timeline.duration())

    def test_legacy_master_mix_and_existing_output_protection(self):
        release = self.job/'deliveries'/'legacy'
        release.mkdir()
        write(release/'timeline.json', self.timeline[:1])
        shutil.copyfile(self.cut, release/'film.mp4')
        store = SimpleNamespace(root=self.root, job=self.store.job, delivery_dir=lambda jid: release)
        path = self.root/'legacy package'
        edit = read(export_edit(store, 'job', path))
        self.assertEqual([t['name'] for t in edit['tracks']['children']], ['Picture','Accepted programme mix'])
        self.assertTrue((path/'programme.wav').is_file())
        self.assertFalse((path/'score.wav').exists())
        with self.assertRaises(FileExistsError):
            export_edit(store, 'job', path)
        self.assertTrue((path/'timeline.otio').is_file())

    def test_broken_release_rolls_back_output(self):
        release = self.job/'deliveries'/'broken'
        release.mkdir()
        write(release/'timeline.json', self.timeline[:1])
        store = SimpleNamespace(root=self.root, job=self.store.job, delivery_dir=lambda jid: release)
        output = self.root/'incomplete'
        with self.assertRaises(ValueError):
            export_edit(store, 'job', output)
        self.assertFalse(output.exists())
        write(release/'timeline.json', [dict(self.timeline[0], file='../other.mp4')])
        with self.assertRaisesRegex(ValueError, '越出目录'):
            export_edit(store, 'job', output)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
