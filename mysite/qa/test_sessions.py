from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIClient
from accounts.models import User
from qa.models import QaRecord

class SessionTests(TestCase):
    def setUp(self):
        self.a=User.objects.create_user(username='alice',phone='1',email='a@example.test')
        self.b=User.objects.create_user(username='bob',phone='2',email='b@example.test')
        self.client=APIClient(); self.client.force_authenticate(self.a)

    def create(self, title='new'):
        r=self.client.post('/api/qa/sessions/',{'title':title},format='json')
        self.assertEqual(r.status_code,201)
        return r.data['ref']

    def test_directory_survives_new_client_login_and_can_be_renamed_pinned_deleted(self):
        ref=self.create('lesson')
        self.assertEqual(self.client.patch(f'/api/qa/sessions/{ref}/',{'title':'renamed','pinned':True},format='json').status_code,200)
        self.client=APIClient(); self.client.force_authenticate(self.a)
        rows=self.client.get('/api/qa/sessions/').data
        if isinstance(rows,dict): rows=rows['results']
        self.assertEqual(rows[0]['title'],'renamed');self.assertTrue(rows[0]['pinned'])
        self.assertEqual(self.client.delete(f'/api/qa/sessions/{ref}/').status_code,204)
        self.assertEqual(self.client.get(f'/api/qa/sessions/{ref}/').status_code,404)

    def test_other_user_cannot_read_edit_delete_or_submit_to_session(self):
        ref=self.create(); self.client.force_authenticate(self.b)
        for method in ['get','patch','delete']:
            r=getattr(self.client,method)(f'/api/qa/sessions/{ref}/',{},format='json')
            self.assertEqual(r.status_code,404)
        with patch('qa.agent_bridge.answer') as answer:
            r=self.client.post('/api/qa/ask/',{'session_ref':ref,'question':'x'},format='json')
            self.assertEqual(r.status_code,404);answer.assert_not_called()

    def test_answer_uses_session_and_history_is_separate_and_chronological(self):
        first=self.create(); second=self.create()
        with patch('qa.agent_bridge.enabled',return_value=True), patch('qa.agent_bridge.answer',return_value={'answer':'ok','references':[],'followups':[]}):
            for ref,q in [(first,'one'),(second,'other'),(first,'two')]:
                response=self.client.post('/api/qa/ask/',{'session_ref':ref,'question':q},format='json')
                self.assertEqual(response.status_code,201)
                self.assertEqual(response.data['session_ref'],ref)
        data=self.client.get('/api/qa/',{'session':first}).data
        if isinstance(data,dict): data=data['results']
        self.assertEqual([r['question'] for r in data],['one','two'])
        self.client.delete(f'/api/qa/sessions/{first}/')
        self.assertEqual(QaRecord.objects.count(),1)
        self.assertEqual(self.client.post('/api/qa/ask/',{'session_ref':first,'question':'late'},format='json').status_code,404)

    def test_migration_preserves_old_records_and_separates_users_and_refs(self):
        import importlib
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from qa.models import QaSession
        from notes.models import Note
        note=Note.objects.create(uploader=self.a,title='old',course='math')
        rows=[QaRecord.objects.create(user=user,note=n,question=q,answer='saved',session_ref=ref)
              for user,n,q,ref in [(self.a,None,'one','old-ref'),(self.a,None,'two','old-ref'),
                  (self.b,None,'other','old-ref'),(self.a,note,'lecture',''),(self.a,None,'general','')]]
        migration=importlib.import_module('qa.migrations.0007_backfill_sessions')
        migration.backfill(apps,SimpleNamespace(connection=connection))
        for row in rows: row.refresh_from_db(); self.assertEqual(row.answer,'saved'); self.assertIsNotNone(row.session_id)
        self.assertEqual(rows[0].session_id,rows[1].session_id)
        self.assertNotEqual(rows[0].session_id,rows[2].session_id)
        self.assertEqual(QaSession.objects.count(),4)
        migration.backfill(apps,SimpleNamespace(connection=connection))
        self.assertEqual(QaSession.objects.count(),4)

    def test_late_answer_cannot_recreate_deleted_history(self):
        from qa.models import QaSession
        from qa.sessions import save_answer
        from rest_framework.exceptions import NotFound
        ref=self.create(); session=QaSession.objects.get(ref=ref)
        self.client.delete(f'/api/qa/sessions/{ref}/')
        with self.assertRaises(NotFound):
            save_answer(session,user=self.a,note=None,question='late',answer='late')
        self.assertEqual(QaRecord.objects.count(),0)
