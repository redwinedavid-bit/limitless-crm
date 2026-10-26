"""Optional separate customer ASGI surface. Staff app stays behind IAP.
Run only after explicitly configuring its HTTPS URL and separate deployment.
"""
import os
from urllib.parse import urlsplit
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import FileResponse,JSONResponse
from sqlalchemy.orm import Session
from . import main as m
from .customer_v111 import TokenIn,CustomerAccept,RequestIn,resolve_grant,grant_view,accept,request_service

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
@app.middleware('http')
async def restricted_surface(request:Request,call_next):
 base=os.getenv('LMS_PORTAL_PUBLIC_URL','').rstrip('/')
 if os.getenv('LMS_CUSTOMER_PORTAL_ENABLED')!='1' or not base.startswith('https://'):return JSONResponse({'detail':'Customer portal disabled'},status_code=404)
 if request.headers.get('host','').lower()!=urlsplit(base).netloc.lower():return JSONResponse({'detail':'Unknown portal host'},status_code=400)
 if request.method=='POST' and request.headers.get('origin','').rstrip('/')!=base:return JSONResponse({'detail':'Invalid origin'},status_code=403)
 response=await call_next(request);response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer';response.headers['X-Frame-Options']='DENY';response.headers['X-Content-Type-Options']='nosniff';return response
@app.get('/')
def page():return FileResponse(m.ROOT/'customer-preview.html')
@app.get('/customer-preview.js')
def script():return FileResponse(m.ROOT/'customer-preview.js',media_type='application/javascript')
@app.post('/api/customer/resolve')
def resolve(data:TokenIn):
 with Session(m.engine) as db:return grant_view(db,resolve_grant(db,data.token))
@app.post('/api/customer/accept')
def approve(data:CustomerAccept):
 with Session(m.engine) as db:return accept(db,data)
@app.post('/api/customer/request')
def service(data:RequestIn):
 with Session(m.engine) as db:return request_service(db,data)
