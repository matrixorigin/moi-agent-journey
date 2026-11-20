import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from journey.parallel_batch import run_parallel


class ParallelBatchTests(unittest.TestCase):
    def test_real_workers_are_bounded_and_one_crash_does_not_skip_queued_cases(self):
        script='''
import json,os,sys,time
from pathlib import Path
case,work,eventfile,public=sys.argv[1:];root=Path(work);root.mkdir(parents=True)
def event(kind):
    fd=os.open(eventfile,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    os.write(fd,(json.dumps([kind,case])+"\\n").encode());os.close(fd)
event('start');print('BUSINESS-DATA-MUST-STAY-IN-PRIVATE-LOG');time.sleep(.15)
if case!='J3-02':
    stage = {'history': {'attempts': 3, 'diagnostic': {'reason': 'http_error', 'http_status': 413}}} if case=='J3-05' else {}
    (root/'batch-result.json').write_text(json.dumps({'cases':[{'case_id':case,'workspace':'journey-3',
      'directory':str(root/case),'failed_stages':list(stage),'stages':stage,'test_status':'completed'}]}))
    destination=Path(public)/case
    destination.mkdir(parents=True)
    (destination/'result.json').write_text(json.dumps({
        'case_id':case,'privacy':'metrics_only','test_status':'completed',
        'benchmark':{'state':'out_of_scope' if case=='J3-03' else 'eligible'},
        'task':{'state':'timeout' if case=='J3-04' else 'completed'},
        'quality_score':80 if case=='J3-01' else None,
        'agent':{'details':'PRIVATE-REPORT-FIELD'},
    }))
event('end');sys.exit(7 if case=='J3-02' else 0)
'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);events=root/'events';real_popen=subprocess.Popen
            def start(command,**kwargs):
                case=json.loads(command[command.index('--cases')+1])[0]
                work=command[command.index('--work')+1]
                return real_popen([sys.executable,'-c',script,case,work,str(events),str(root/'public')],**kwargs)
            stdout=io.StringIO()
            with (patch('journey.parallel_batch.subprocess.Popen', side_effect=start),
                  patch('journey.parallel_batch.PROGRESS_INTERVAL_SECONDS', .05),
                  contextlib.redirect_stdout(stdout)):
                code=run_parallel(['J3-01','J3-02','J3-03','J3-04','J3-05'],'journey-3',root/'work',root/'public',os.environ.copy(),2)
            self.assertEqual(code,1)
            progress = stdout.getvalue()
            self.assertNotIn('BUSINESS-DATA', progress)
            self.assertNotIn('PRIVATE-REPORT-FIELD', progress)
            self.assertGreaterEqual(progress.count('Progress |'), 3)
            self.assertIn('Progress | finished 0/5 | running 2 | queued 3', progress)
            self.assertIn('Progress | finished 5/5 | running 0 | queued 0', progress)
            self.assertRegex(progress, r'Slot 01 \| running: J3-01 \(\d+s\) \| finished: —')
            self.assertRegex(progress, r'Slot 01 \| finished J3-01 \(\d+s\) \| delivered, score 80/100')
            self.assertRegex(progress, r'Slot 02 \| finished J3-02 \(\d+s\) \| framework error')
            self.assertIn('J3-03 [out of scope]', progress)
            self.assertIn('history attempts: 3', progress)
            self.assertIn('history={"reason":"http_error","http_status":413}', progress)
            self.assertIn('J3-04 [agent timeout]', progress)
            active=peak=0;started=[]
            for kind,case in map(json.loads,events.read_text().splitlines()):
                active+=1 if kind=='start' else -1;peak=max(peak,active)
                if kind=='start':started.append(case)
            self.assertEqual(peak,2);self.assertEqual(active,0)
            self.assertCountEqual(started,['J3-01','J3-02','J3-03','J3-04','J3-05'])
            rows=json.loads((root/'work/batch-result.json').read_text())['cases']
            self.assertEqual([r['case_id'] for r in rows],['J3-01','J3-02','J3-03','J3-04','J3-05'])
            self.assertEqual(rows[1]['failed_stages'],['worker_result_missing'])
            self.assertEqual(rows[-2]['failed_stages'],[])
            self.assertEqual(rows[-1]['failed_stages'],['history'])


if __name__=='__main__':unittest.main()
