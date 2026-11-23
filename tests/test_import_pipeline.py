"""Exercise the public import command through a sealed release and real HTTP uploads."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image

from factory import archive, media
from factory.common import read, write


class StudioProductionTest(unittest.TestCase):
    def test_real_cli_import_isolated_assets_and_authored_clip_links_reach_published_movie(self):
        import test_factory
        env = test_factory.Environment()
        env.setUp()
        self.addCleanup(env.doCleanups)
        env.reject_first = False
        root = env.cfg['data'].parent
        legacy = root/'Studio 项目'
        legacy.mkdir()
        source = legacy/'anchor.png'
        Image.new('RGB', (40, 40), 'tomato').save(source)
        copied_bytes = source.read_bytes()
        # A different same-named shared image and an unrelated shared file must stay out.
        shared = (env.cfg['assets']/'anchor.png').read_bytes()
        (env.cfg['assets']/'unrelated.png').write_bytes(shared)
        self.assertNotEqual(copied_bytes, shared)
        archive_map = root/'approved.json'
        write(archive_map, {'last_issued':9, 'clips':{'C006':'fixture.mp4'}})
        archive.import_clips(env.store, archive_map)
        clip_tail = archive.references(env.store, 'C006')['C006'].read_bytes()
        project = legacy/'project.json'
        write(project, {'project_id':'proj_test','name':'先导片','script':'三个工位，最后显现 END。',
                        'seconds':6,'assets':[{'path':'anchor.png','description':'测试温室的橙色门'}]})
        mapping = legacy/'links.json'
        write(mapping, {'mode':'auto', 'music':True, 'links':[
            {'shot':1, 'first':'anchor.png'}, {'shot':2, 'first':'C006'},
            {'shot':3, 'first':'previous', 'last':'anchor.png'}]})
        config_path = root/'config.json'
        write(config_path, {k:str(v) if isinstance(v,Path) else v for k,v in env.cfg.items()})
        command = [sys.executable, str(Path(__file__).resolve().parents[1]/'film.py'), '--config', str(config_path),
                   'import-studio', str(project), '--mapping', str(mapping), '--enqueue']
        submitted = subprocess.check_output(command, env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        jid = json.loads(submitted)['job_id']
        source.unlink()
        env.execute(jid)
        self.assertEqual(env.store.job(jid)['state'], 'done')
        plan_request = next(b['messages'][1]['content'] for b,_,_ in env.calls
                            if 'You direct the supplied film' in b['messages'][0]['content'])
        plan_payload = json.loads(plan_request[0]['text'] if isinstance(plan_request,list) else plan_request)
        self.assertEqual(set(plan_payload['assets']), {'anchor.png','C006'})
        self.assertEqual(plan_payload['assets']['anchor.png'], '测试温室的橙色门')
        self.assertEqual([set(g['1']['inputs']) & {'first','last'} for g in env.submitted],
                         [{'first'},{'first'},{'first','last'}])
        self.assertEqual(len(env.uploaded),4)
        self.assertIn(copied_bytes,env.uploaded[0])
        self.assertIn(clip_tail,env.uploaded[1])
        self.assertIn(copied_bytes,env.uploaded[3])
        take2 = next(t for t in env.store.takes(jid) if t['shot']==2 and t['selected'])
        self.assertIn((Path(take2['path'])/'tail.png').read_bytes(),env.uploaded[2])
        self.assertNotIn(shared,env.uploaded[0])
        release = env.store.delivery_dir(jid)
        timeline = read(release/'timeline.json')
        self.assertEqual([(s['start'],s['end']) for s in timeline],
                         [('anchor.png',''),('C006',''),('previous','anchor.png')])
        self.assertEqual([s['code'] for s in timeline],['C010','C011','C012'])
        self.assertTrue(read(release/'acceptance.json')['editorial']['passed'])
        self.assertEqual(media.stream(media.probe(release/'film.mp4',count=True),'video')['nb_read_frames'],'144')
        self.assertEqual(env.engine.cfg['assets'],env.cfg['assets'])


if __name__ == '__main__':
    unittest.main()
