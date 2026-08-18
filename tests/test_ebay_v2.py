import json
from dataclasses import replace
from cryptography.fernet import Fernet
import pytest
from config import Config
from db import Database
from integrations.ebay.errors import EbayError
from integrations.ebay.oauth import OAuthManager, SCOPES
from integrations.ebay.photos import PhotoPublisher
from integrations.ebay.service import EbayService

class Response:
    def __init__(self,status=204): self.status_code=status
class HTTP:
    def __init__(self): self.calls=[]
    def put(self,*args,**kwargs): self.calls.append((args,kwargs)); return Response()
class Client:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs)); value=self.responses.pop(0)
        if isinstance(value,Exception): raise value
        return value

def make(tmp_path):
    cfg=Config(database_path=str(tmp_path/'db'),upload_directory=str(tmp_path/'uploads'),encryption_key=Fernet.generate_key().decode(),client_id='id',client_secret='secret',runame='name',location_key='loc',fulfillment_policy_id='f',payment_policy_id='p',return_policy_id='r',category_id='1',media_public_base_url='https://cdn.test/items',media_upload_url='https://upload.test/items')
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
    http=HTTP(); urls=PhotoPublisher(cfg,db,http).publish(item)
    assert len(http.calls)==2 and urls==json.loads(db.get_item(item['id'])['photo_urls'])
    PhotoPublisher(cfg,db,http).publish(db.get_item(item['id'])); assert len(http.calls)==2

def test_atomic_edit_keeps_photos_and_marks_pending(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=[])
    edited=db.update_item_and_photos(item['id'],{'title':'new','description':'d','price':'2.00','quantity':2,'condition':'USED','category':'2','brand':'B','size':'M','color':'R','item_specifics':{'Brand':['B']}})
    assert edited['title']=='new' and edited['item_specifics']=={'Brand':['B']} and edited['local_revision']==1 and edited['ebay_sync_state']=='pending'

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
