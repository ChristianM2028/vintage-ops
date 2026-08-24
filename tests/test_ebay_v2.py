import json
from dataclasses import replace
from cryptography.fernet import Fernet
import pytest
from config import Config
from db import Database
from integrations.ebay.errors import EbayError
from integrations.ebay.oauth import OAuthManager, SCOPES
from integrations.ebay.photos import EbayImagePublisher
from integrations.ebay.service import EbayService

class MediaClient:
    def __init__(self): self.calls=[]
    def request(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs))
        number=len(self.calls)
        return {'imageId':f'IMAGE-{number}','imageUrl':f'https://i.ebayimg.com/images/g/eps-{number}/s-l1600.jpg'}
class PartialMediaClient(MediaClient):
    def __init__(self): super().__init__(); self.fail=True
    def request(self,method,path,**kwargs):
        name=kwargs['files']['image'][0]
        self.calls.append((method,path,kwargs))
        if name=='b' and self.fail: raise EbayError('remote','Safe upload failure')
        return {'imageId':f'IMAGE-{name}','imageUrl':f'https://i.ebayimg.com/{name}.jpg'}
class Client:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs)); value=self.responses.pop(0)
        if isinstance(value,Exception): raise value
        return value

def make(tmp_path):
    cfg=Config(database_path=str(tmp_path/'db'),upload_directory=str(tmp_path/'uploads'),encryption_key=Fernet.generate_key().decode(),client_id='id',client_secret='secret',runame='name',location_key='loc',fulfillment_policy_id='f',payment_policy_id='p',return_policy_id='r',category_id='1',marketplace_mock_mode=False)
    db=Database(cfg.database_path); db.migrate(); (tmp_path/'uploads').mkdir()
    return cfg,db

def test_oauth_requires_all_configuration_and_current_scopes(tmp_path):
    cfg,db=make(tmp_path)
    with pytest.raises(EbayError) as error: OAuthManager(replace(cfg,client_secret=''),db,None).begin()
    assert error.value.kind=='configuration' and 'EBAY_CLIENT_SECRET' in str(error.value)
    f=Fernet(cfg.encryption_key.encode()); stamp='2999-01-01T00:00:00+00:00'
    with db.transaction() as c: c.execute('INSERT INTO ebay_tokens VALUES(1,?,?,?,?,?,?)',(f.encrypt(b'r'),f.encrypt(b'a'),stamp,stamp,'https://api.ebay.com/oauth/api_scope',stamp))
    with pytest.raises(EbayError) as error: OAuthManager(cfg,db,None).access_token()
    assert error.value.kind=='reauthorization_required' and 'sell.fulfillment' in SCOPES

def test_photo_publication_reuses_fingerprint_and_preserves_order(tmp_path):
    cfg,db=make(tmp_path); (tmp_path/'uploads'/'a.jpg').write_bytes(b'one'); (tmp_path/'uploads'/'b.jpg').write_bytes(b'two')
    item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=[],photos=[{'filename':'a.jpg','original_filename':'a'},{'filename':'b.jpg','original_filename':'b'}])
    item=db.update_item(item['id'],ebay_integration_mode='ebay_sandbox')
    client=MediaClient(); urls=EbayImagePublisher(cfg,db,client).publish(item)
    saved=db.get_item(item['id'])
    assert len(client.calls)==2 and urls==json.loads(saved['photo_urls'])
    assert [p['ebay_image_id'] for p in saved['photos']]==['IMAGE-1','IMAGE-2']
    assert all('image' in call[2]['files'] for call in client.calls)
    EbayImagePublisher(cfg,db,client).publish(saved); assert len(client.calls)==2

def test_atomic_edit_keeps_photos_and_marks_pending(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=[])
    edited=db.update_item_and_photos(item['id'],{'title':'new','description':'d','price':'2.00','quantity':2,'condition':'USED','category':'2','brand':'B','size':'M','color':'R','item_specifics':{'Brand':['B']}})
    assert edited['title']=='new' and edited['item_specifics']=={'Brand':['B']} and edited['local_revision']==1 and edited['ebay_sync_state']=='pending'

def test_eps_partial_failure_is_incremental_and_promotes_only_when_complete(tmp_path):
    cfg,db=make(tmp_path); (tmp_path/'uploads'/'a.jpg').write_bytes(b'one'); (tmp_path/'uploads'/'b.jpg').write_bytes(b'two')
    old=['https://i.ebayimg.com/old.jpg']
    item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=old,photos=[{'filename':'a.jpg','original_filename':'a'},{'filename':'b.jpg','original_filename':'b'}])
    item=db.update_item(item['id'],ebay_integration_mode='ebay_production')
    client=PartialMediaClient()
    with pytest.raises(EbayError) as error: EbayImagePublisher(cfg,db,client).publish(item)
    assert error.value.kind=='photo_partial_failure' and error.value.details[0]['filename']=='b'
    partial=db.get_item(item['id'])
    assert json.loads(partial['photo_urls'])==old
    assert partial['photos'][0]['ebay_image_id']=='IMAGE-a' and partial['photos'][1]['ebay_upload_error']=='Safe upload failure'
    client.fail=False
    EbayImagePublisher(cfg,db,client).publish(partial)
    assert [call[2]['files']['image'][0] for call in client.calls]==['a','b','b']
    complete=db.get_item(item['id'])
    assert json.loads(complete['photo_urls'])==['https://i.ebayimg.com/a.jpg','https://i.ebayimg.com/b.jpg']
    assert all(p['ebay_upload_error'] is None for p in complete['photos'])

def test_photo_mutation_retains_eps_metadata_and_last_good_urls(tmp_path):
    cfg,db=make(tmp_path)
    item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://old'],photos=[{'filename':'a.jpg','original_filename':'a'},{'filename':'b.jpg','original_filename':'b'}])
    first,second=item['photos']; db.update_photo_eps(first['id'],'I','https://eps/a','hash',None)
    values={'title':'x','description':'x','price':'1.00','quantity':1,'condition':'NEW','category':'1','category_name':'x','brand':'','size':'','color':'','item_specifics':{}}
    changed,removed=db.mutate_item_photos(item['id'],values,[{'id':first['id']},{'filename':'c.jpg','original_filename':'c'}])
    assert removed==['b.jpg'] and changed['photos'][0]['ebay_image_id']=='I'
    assert json.loads(changed['photo_urls'])==['https://old'] and changed['photo_set_revision']==1

def test_ambiguous_create_recovers_remote_offer(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x'])
    remote=EbayError('remote','timeout'); client=Client([{},remote,{'offers':[{'offerId':'O','sku':item['sku'],'marketplaceId':'EBAY_US'}]},{'listingId':'L'}])
    result=EbayService(cfg,db,client).list(item)
    assert result['ebay_offer_id']=='O' and result['ebay_listing_id']=='L'

def test_order_reconciliation_is_idempotent_and_removes_sold_stock(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=[],sku='SKU')
    db.update_item(item['id'],ebay_sku='SKU',ebay_listing_id='LIST',status='Active',storefront_visible=1)
    order={'orderId':'ORDER','orderPaymentStatus':'PAID','orderFulfillmentStatus':'NOT_STARTED','lineItems':[{'lineItemId':'LINE','sku':'SKU','legacyItemId':'LIST','quantity':1}]}
    service=EbayService(cfg,db,Client([{'orders':[order]},{'orders':[order]}])); service.reconcile_orders(); service.reconcile_orders()
    sold=db.get_item(item['id']); assert sold['status']=='Sold' and sold['quantity']==0 and sold['storefront_visible']==0
    with db.connect() as c: assert c.execute('select count(*) from ebay_order_lines').fetchone()[0]==1
