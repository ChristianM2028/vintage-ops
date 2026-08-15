from automation.specs.ebay import create_listing_specs

class EbayBrowserAdapter:
    """eBay vertical slice based only on the supplied authorized flow capture."""
    name="ebay"; version="2026-08-capture-1"; concurrency_limit=1
    route_families=("selling_home","listing_start","category_picker","find_match","condition","listing_form","listing_result")
    def field_specs(self,item,operation):
        if operation not in {"list","update"}: return ()
        return create_listing_specs(item)
    def route_name(self,url):
        value=url.casefold()
        if "/sl/sell" in value:return "listing_start"
        if "ebay.com" in value:return "ebay_authenticated"
        return "unknown"
    async def bootstrap(self,page,operation):
        # Navigation uses declared accessible names, never routine CSS selectors.
        await page.goto("https://www.ebay.com/")
        sell=page.get_by_role("link",name="Sell",exact=True)
        await sell.hover()
        action=page.get_by_role("link",name="List an item",exact=True)
        if await action.count()!=1: action=page.get_by_role("button",name="List an item",exact=True)
        await action.click()
    async def submit_once(self,page,run_id):
        if await page.evaluate("key => sessionStorage.getItem(key)",f"vintage-submit:{run_id}"):
            return {"uncertain":True}
        button=page.get_by_role("button",name="List it",exact=True)
        if await button.count()!=1: return {"uncertain":True}
        await page.evaluate("key => sessionStorage.setItem(key, 'clicked')",f"vintage-submit:{run_id}")
        before=page.url; await button.click()
        try: await page.wait_for_url(lambda url:str(url)!=before,timeout=15_000)
        except Exception:return {"uncertain":True}
        return await self.extract_remote(page)
    async def extract_remote(self,page):
        return {"remote_id":await page.remote_listing_id(),"remote_url":page.url,"uncertain":False}
    async def reconcile(self,page,item):
        return await page.find_listing_by_marker(item["sku"],item["title"])
