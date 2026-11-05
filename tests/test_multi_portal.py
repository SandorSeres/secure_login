"""Tests operate entirely on in-memory sqlite, never production data."""
import os
import unittest
os.environ.update({
    'SECRET_KEY': 'A'*48, 'CSRF_KEY': 'B'*48,
    'DATABASE_URL':'sqlite://',
    'APP_HOST':'https://localhost',
    'ROLES_JSON':'[{"id":"user","name":"User","default":true}]',
    'PORTALS_JSON':'[{"id":"first","name":"First","url":"https://research.example.invalid/","upstream":"127.0.0.1:8000"},{"id":"second","name":"Second","url":"https://research.example.invalid/","upstream":"127.0.0.1:8001"}]',
    'ADMIN_USERNAMES':'["admin"]',
    'ENABLE_MULTI_PORTAL':'false',
})
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models import Base, Role, Portal, User, UserPortalAccess
from app.authorization import portals_for_user, assigned_portal, require_portal_access

class MultiPortalTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db=Session(self.engine)
        self.db.add(Role(id='user',name='User',is_default=True,sort_order=0))
        self.db.add_all([Portal(id='first',name='First',enabled=True,sort_order=0),Portal(id='second',name='Second',enabled=True,sort_order=1)])
        self.db.add(User(id=1,username='alice',email='alice@example.invalid',password_hash='unused',role_id='user'))
        self.db.add(User(id=2,username='bob',email='bob@example.invalid',password_hash='unused',role_id='user'))
        self.db.commit()

    def tearDown(self): self.db.close();self.engine.dispose()

    def test_many_to_many_and_deny_cross_access(self):
        self.db.add_all([UserPortalAccess(user_id=1,portal_id='first'),UserPortalAccess(user_id=1,portal_id='second'),UserPortalAccess(user_id=2,portal_id='first')]);self.db.commit()
        a=self.db.get(User,1);b=self.db.get(User,2)
        self.assertEqual([p.id for p in portals_for_user(a,self.db)],['first','second'])
        self.assertEqual(require_portal_access(b,'first',self.db).id,'first')
        with self.assertRaises(HTTPException): require_portal_access(b,'second',self.db)
        with self.assertRaises(HTTPException): assigned_portal(a,self.db)
        self.assertEqual(assigned_portal(a,self.db,'second').upstream,'127.0.0.1:8001')
        with self.assertRaises(HTTPException): assigned_portal(a,self.db,'invalid')

    def test_single_portal_backwards_compatible(self):
        self.db.add(UserPortalAccess(user_id=1,portal_id='first'));self.db.commit()
        self.assertEqual(assigned_portal(self.db.get(User,1),self.db).id,'first')
        with self.assertRaises(HTTPException): assigned_portal(self.db.get(User,2),self.db)

if __name__ == '__main__': unittest.main()
