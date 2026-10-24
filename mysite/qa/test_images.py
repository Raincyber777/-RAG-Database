import io, json, tempfile
from unittest.mock import patch
from PIL import Image
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import default_storage
from rest_framework.test import APIClient
from accounts.models import User
from qa.models import QaRecord, QaSession


def picture(name='board.png'):
    buf=io.BytesIO();Image.new('RGB',(64,32),'red').save(buf,format='PNG')
    return SimpleUploadedFile(name,buf.getvalue(),content_type='image/png')

class ImageAskTests(TestCase):
    def setUp(self):
        self.enterContext(override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.InMemoryStorage'}}))
        self.user=User.objects.create_user(username='owner',email='o@test.invalid',phone='1')
        self.other=User.objects.create_user(username='other',email='b@test.invalid',phone='2')
        self.session=QaSession.objects.create(user=self.user)
        self.client=APIClient();self.client.force_authenticate(self.user)

    def ask(self, **data):
        return self.client.post('/qa/ask/stream/',{'session_ref':str(self.session.ref),**data},format='multipart')

    def events(self,r):
        try: raw=b''.join(r.streaming_content).decode()
        finally:r.close()
        return [(b.splitlines()[0][7:],json.loads(b.splitlines()[1][6:])) for b in raw.strip().split('\n\n')]

    def test_image_only_is_accepted(self):
        r=self.ask(images=[picture()])
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.streaming)
        r.close()

    def test_invalid_file_is_rejected_before_search_or_model(self):
        with patch('qa.agent_bridge.enabled',side_effect=AssertionError('text route')):
            r=self.ask(question='what?',images=[SimpleUploadedFile('fake.png',b'not image',content_type='image/png')])
        self.assertEqual(r.status_code,400)
        self.assertEqual(QaRecord.objects.count(),0)

    def test_plain_empty_message_remains_invalid(self):
        self.assertEqual(self.ask(question='').status_code,400)

    def test_stream_sends_real_image_to_vision_and_restores_history(self):
        import httpx
        from qa import images
        real_client=httpx.Client
        requests=[]
        def handler(request):
            body=json.loads(request.content);requests.append(body)
            self.assertEqual(body['model'],'qwen3-vl-plus')
            content=body['messages'][1]['content']
            self.assertEqual(content[0]['text'],'what color?')
            self.assertTrue(content[2]['image_url']['url'].startswith('data:image/jpeg;base64,'))
            frames=[{'choices':[{'delta':{'content':'红色'},'finish_reason':None}]},
                    {'choices':[{'delta':{'content':'图片'},'finish_reason':'stop'}]}]
            return httpx.Response(200,text=''.join('data: '+json.dumps(f)+'\n\n' for f in frames)+'data: [DONE]\n\n')
        with patch('qa.images.httpx.Client',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw)), \
                patch('qa.agent_bridge.enabled',side_effect=AssertionError('must not search')):
            response=self.ask(question='what color?',images=[picture()])
            self.assertEqual(response['X-QA-Engine'],'vision')
            events=self.events(response)
        self.assertEqual([data['text'] for ev,data in events if ev=='delta'],['红色','图片'])
        self.assertEqual(events[-1][0],'done')
        record=QaRecord.objects.get();self.assertEqual(record.answer,'红色图片')
        self.assertTrue(default_storage.exists(record.images[0]['path']))
        self.assertNotIn('path',events[-1][1]['attachments'][0])
        history=self.client.get('/qa/',{'session':str(self.session.ref)}).data
        if isinstance(history,dict):history=history['results']
        self.assertEqual(history[0]['attachments'][0]['name'],'board.png')
        self.assertTrue(history[0]['attachments'][0]['url'])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/qa/sessions/{self.session.ref}/')
        self.assertFalse(default_storage.exists(record.images[0]['path']))

    def test_truncated_stream_clears_draft_and_cleans_files(self):
        import httpx
        real_client=httpx.Client
        response=httpx.Response(200,text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        with patch('qa.images.httpx.Client',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(lambda r:response),**kw)):
            events=self.events(self.ask(question='describe',images=[picture()]))
        self.assertEqual(events[-2],('replace',{'text':''}))
        self.assertEqual(events[-1][0],'error');self.assertEqual(QaRecord.objects.count(),0)
        self.assertEqual(default_storage.listdir('qa/images')[1],[])

    def test_regeneration_requires_same_owner_and_session(self):
        from django.core.files.base import ContentFile
        path=default_storage.save('qa/images/source.jpg',ContentFile(picture().read()))
        record=QaRecord.objects.create(user=self.user,session=self.session,session_ref=str(self.session.ref),
            question='q',answer='a',images=[{'name':'board.png','path':path,'size':100}])
        other_session=QaSession.objects.create(user=self.user)
        self.assertEqual(self.client.post('/qa/ask/',{'question':'q','session_ref':str(other_session.ref),'image_record_id':record.pk},format='json').status_code,404)
        self.client.force_authenticate(self.other)
        foreign=QaSession.objects.create(user=self.other)
        self.assertEqual(self.client.post('/qa/ask/',{'question':'q','session_ref':str(foreign.ref),'image_record_id':record.pk},format='json').status_code,404)
        self.client.force_authenticate(self.user)
        with patch('qa.images.vision_tokens',side_effect=lambda *args:(x for x in ['new answer'])):
            r=self.client.post('/qa/ask/',{'question':'q','session_ref':str(self.session.ref),'image_record_id':record.pk},format='json')
        self.assertEqual(r.status_code,201)
        self.assertEqual(r.data['answer'],'new answer')
        self.assertNotEqual(QaRecord.objects.get(pk=r.data['id']).images[0]['path'],path)

    def test_limits_and_file_type(self):
        self.assertEqual(self.ask(images=[picture() for _ in range(9)]).status_code,400)
        self.assertEqual(self.ask(images=[SimpleUploadedFile('huge.png',b'x'*(10*1024*1024+1))]).status_code,400)
        self.assertEqual(self.ask(images=[SimpleUploadedFile('file.pdf',b'%PDF')]).status_code,400)
        self.assertEqual(self.ask(images=[picture()],image_record_id=1).status_code,400)

    def test_disconnect_removes_uploaded_files_without_saving_answer(self):
        with patch('qa.images.vision_tokens',side_effect=lambda *args:(x for x in ['partial','rest'])):
            response=self.ask(images=[picture()]); iterator=iter(response.streaming_content)
            next(iterator); next(iterator)
            self.assertTrue(default_storage.listdir('qa/images')[1])
            response.close()
        self.assertEqual(QaRecord.objects.count(),0)
        self.assertEqual(default_storage.listdir('qa/images')[1],[])

    def test_history_url_failure_rolls_back_record_and_removes_files(self):
        with patch('qa.images.vision_tokens', side_effect=lambda *args: (x for x in ['answer'])), \
                patch.object(default_storage, 'url', side_effect=OSError('unavailable')):
            events = self.events(self.ask(images=[picture()]))
        self.assertEqual(events[-1][0], 'error')
        self.assertEqual(QaRecord.objects.count(), 0)
        self.assertEqual(default_storage.listdir('qa/images')[1], [])

    def test_missing_history_image_returns_readable_error(self):
        record = QaRecord.objects.create(user=self.user, session=self.session, question='q', answer='a',
            images=[{'name': 'missing.jpg', 'path': 'missing.jpg', 'size': 10}])
        response = self.client.post('/qa/ask/', {'session_ref': str(self.session.ref),
            'image_record_id': record.pk}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertIn('历史图片', str(response.data))
