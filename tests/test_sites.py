import unittest
from pydantic import ValidationError
from app.api.sites import SiteFields
from app.api.tabs import SiteSelection


class SitesTests(unittest.TestCase):
    def test_fields_and_normalization(self):
        site=SiteFields(title='  دستیار من  ',url='https://example.com',icon='🤖')
        self.assertEqual(site.title,'دستیار من')
        self.assertEqual(site.url,'https://example.com/')
        self.assertTrue(site.is_active)
        self.assertEqual(SiteFields(title='Local',url='http://127.0.0.1:1234/path?q=x').icon,'')

    def test_invalid_urls(self):
        for url in ['javascript:alert(1)','file:///etc/passwd','data:text/html,test','ftp://example.com',
                    'https://user:pass@example.com','https://example.com/ hello','https://example.com\\evil',
                    '//example.com','example.com','https://example.com:99999']:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                SiteFields(title='Test',url=url)

    def test_invalid_fields(self):
        for fields in [{'title':' '},{'title':'x'*81},{'icon':'x'*17},{'title':'a\x00b'}, {'icon':'\u202e'}, {'extra':'value'}]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                SiteFields(**{'title':'Test','url':'https://example.com',**fields})

    def test_selection_only_takes_id(self):
        for fields in [{'site_id':1,'url':'https://evil.test'},{'site_id':0},{'site_id':True},{'site_id':'1'},{'site_id':10**30}]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError): SiteSelection(**fields)


if __name__=='__main__': unittest.main()
