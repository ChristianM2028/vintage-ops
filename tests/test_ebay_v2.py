import json
from dataclasses import replace
from cryptography.fernet import Fernet
import pytest
from config import Config
from db import Database
from integrations.ebay.errors import EbayError
from integrations.ebay.errors import parse_error
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

class Response:
    def __init__(self,status,data):
        self.status_code=status; self.data=data; self.headers={}; self.content=b'error'
    def json(self): return self.data

def make(tmp_path):
    cfg=Config(database_path=str(tmp_path/'db'),upload_directory=str(tmp_path/'uploads'),encryption_key=Fernet.generate_key().decode(),client_id='id',client_secret='secret',runame='name',location_key='loc',fulfillment_policy_id='f',payment_policy_id='p',return_policy_id='r',category_id='1',ebay_provider_mode="sandbox")
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
    assert error.value.kind=='photo_partial_failure' and error.value.details[0]['photo_id']==item['photos'][1]['id']
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
    offer={'offerId':'O','sku':item['sku'],'marketplaceId':'EBAY_US','format':'FIXED_PRICE','categoryId':'1','merchantLocationKey':'loc','listingPolicies':{'fulfillmentPolicyId':'f','paymentPolicyId':'p','returnPolicyId':'r'}}
    remote=EbayError('remote','timeout'); client=Client([{}, {'offers':[]},remote,{'offers':[offer]},{'listingId':'L'}])
    result=EbayService(cfg,db,client).list(item)
    assert result['ebay_offer_id']=='O' and result['ebay_listing_id']=='L'

@pytest.mark.parametrize('failure',[EbayError('remote','timeout'),EbayError('remote','unavailable',status=503)])
def test_committed_create_response_failure_is_reconciled_without_retry(tmp_path,failure):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x'])
    offer={'offerId':'O','sku':item['sku'],'marketplaceId':'EBAY_US','format':'FIXED_PRICE','categoryId':'1','merchantLocationKey':'loc','listingPolicies':{'fulfillmentPolicyId':'f','paymentPolicyId':'p','returnPolicyId':'r'}}
    client=Client([{}, {'offers':[]},failure,{'offers':[offer]},{'listingId':'L'}])

    result=EbayService(cfg,db,client).list(item)

    creates=[call for call in client.calls if call[0]==('POST','/sell/inventory/v1/offer')]
    assert len(creates)==1 and result['ebay_offer_id']=='O' and result['ebay_listing_id']=='L'

def test_consecutive_items_send_unique_exact_skus_and_create_one_offer_each(tmp_path):
    cfg,db=make(tmp_path)
    items=[db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x']) for _ in range(2)]
    client=Client([{}, {'offers':[]},{'offerId':'O5'},{'listingId':'L5'}, {}, {'offers':[]},{'offerId':'O6'},{'listingId':'L6'}])
    service=EbayService(cfg,db,client)

    for item in items: service.list(item)

    inventory_puts=[call for call in client.calls if call[0][0]=='PUT' and '/inventory_item/' in call[0][1]]
    creates=[call for call in client.calls if call[0]==('POST','/sell/inventory/v1/offer')]
    lookups=[call for call in client.calls if call[0][0]=='GET' and '/offer?' in call[0][1]]
    assert [call[0][1].rsplit('/',1)[-1] for call in inventory_puts]==['VO-000001','VO-000002']
    assert [call[1]['json']['sku'] for call in creates]==['VO-000001','VO-000002']
    assert all(call[1]['json']['marketplaceId']=='EBAY_US' for call in creates)
    assert [call[0][1] for call in lookups]==['/sell/inventory/v1/offer?sku=VO-000001&marketplace_id=EBAY_US','/sell/inventory/v1/offer?sku=VO-000002&marketplace_id=EBAY_US']
    assert len(creates)==2

def test_existing_remote_offer_repairs_local_item_without_create_or_delete(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(sku='VO-000006',title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x'])
    offer={'offerId':'O6','sku':'VO-000006','marketplaceId':'EBAY_US','format':'FIXED_PRICE','categoryId':'1','merchantLocationKey':'loc','listingPolicies':{'fulfillmentPolicyId':'f','paymentPolicyId':'p','returnPolicyId':'r'}}
    client=Client([{}, {'offers':[offer]},{'listingId':'L6'}])

    result=EbayService(cfg,db,client).list(item)

    assert result['ebay_offer_id']=='O6' and result['ebay_listing_id']=='L6'
    assert not any(call[0][0] in ('POST','DELETE') and call[0][1]=='/sell/inventory/v1/offer' for call in client.calls)

def test_real_get_offers_shape_adopts_unpublished_offer_without_create(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x'])
    # getOffers does not necessarily echo merchantLocationKey and may return
    # additional/normalized listing-policy data that was absent at creation.
    offer={'offerId':'O-REAL','sku':item['sku'],'marketplaceId':'EBAY_US','format':'FIXED_PRICE',
        'availableQuantity':1,'categoryId':'001','status':'UNPUBLISHED',
        'listingPolicies':{'fulfillmentPolicyId':'f','paymentPolicyId':'p','returnPolicyId':'r',
            'eBayPlusIfEligible':False},
        'pricingSummary':{'price':{'value':'1.00','currency':'USD'}}}
    client=Client([{}, {'total':1,'size':1,'offers':[offer]}, {'listingId':'L-REAL'}])

    result=EbayService(cfg,db,client).list(item)

    assert result['ebay_offer_id']=='O-REAL' and result['ebay_listing_id']=='L-REAL'
    assert not any(call[0]==('POST','/sell/inventory/v1/offer') for call in client.calls)

def test_duplicate_offer_response_queries_state_and_fails_closed(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x'])
    duplicate=EbayError('conflict','Offer entity already exists.',status=409)
    client=Client([{}, {'offers':[]},duplicate,{'offers':[]}])

    with pytest.raises(EbayError) as error: EbayService(cfg,db,client).list(item)

    creates=[call for call in client.calls if call[0]==('POST','/sell/inventory/v1/offer')]
    assert len(creates)==1 and error.value.kind=='manual_resolution'
    assert item['sku'] in str(error.value) and 'EBAY_US' in str(error.value) and 'no create retry' in str(error.value)

def test_http_400_structured_duplicate_offer_response_is_reconciled(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x'])
    duplicate=parse_error(Response(400,{'errors':[{'errorId':25002,'domain':'API_INVENTORY',
        'category':'REQUEST','message':'A user error has occurred.',
        'longMessage':'Offer entity already exists.'}]}))
    offer={'offerId':'O-400','sku':item['sku'],'marketplaceId':'EBAY_US','format':'FIXED_PRICE','status':'UNPUBLISHED'}
    client=Client([{}, {'offers':[]}, duplicate, {'offers':[offer]}, {'listingId':'L-400'}])

    result=EbayService(cfg,db,client).list(item)

    assert duplicate.status==400 and duplicate.details[0]['errorId']==25002
    assert result['ebay_offer_id']=='O-400' and result['ebay_listing_id']=='L-400'
    assert sum(call[0]==('POST','/sell/inventory/v1/offer') for call in client.calls)==1

def test_offer_discovery_fails_closed_when_returned_identity_is_missing(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=['https://cdn/x'])
    client=Client([{}, {'offers':[{'offerId':'UNKNOWN','marketplaceId':'EBAY_US','status':'UNPUBLISHED'}]}])

    with pytest.raises(EbayError) as error: EbayService(cfg,db,client).list(item)

    assert error.value.kind=='manual_resolution'
    assert not any(call[0]==('POST','/sell/inventory/v1/offer') for call in client.calls)

def test_order_reconciliation_is_idempotent_and_removes_sold_stock(tmp_path):
    cfg,db=make(tmp_path); item=db.create_item(title='x',description='x',price='1',currency='USD',quantity=1,condition='NEW',category='1',photo_urls=[],sku='SKU')
    db.update_item(item['id'],ebay_sku='SKU',ebay_listing_id='LIST',status='Active',storefront_visible=1)
    order={'orderId':'ORDER','orderPaymentStatus':'PAID','orderFulfillmentStatus':'NOT_STARTED','lineItems':[{'lineItemId':'LINE','sku':'SKU','legacyItemId':'LIST','quantity':1}]}
    service=EbayService(cfg,db,Client([{'orders':[order]},{'orders':[order]}])); service.reconcile_orders(); service.reconcile_orders()
    sold=db.get_item(item['id']); assert sold['status']=='Sold' and sold['quantity']==0 and sold['storefront_visible']==0
    with db.connect() as c: assert c.execute('select count(*) from ebay_order_lines').fetchone()[0]==1
