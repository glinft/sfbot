# encoding:utf-8

import jwt
import datetime
import time
from flask import jsonify, request
from common import const
from common.redis import RedisSingleton
from config import channel_conf


class Auth():
    def __init__(self, login):
    # argument 'privilegeRequired' is to set up your method's privilege
    # name
        self.login = login
        super(Auth, self).__init__()

    @staticmethod
    def encode_auth_token(username, password, login_time):
        """
        生成认证Token
        :param username: str
        :param password: str
        :param login_time: datetime
        :return: string
        """
        try:
            payload = {
                'iss': 'ken',  # 签名
                'exp': datetime.datetime.utcnow() + datetime.timedelta(days=0, hours=10),  # 过期时间
                'iat': datetime.datetime.utcnow(),  # 开始时间
                'data': {
                    'id': username,
                    'password': password,
                    'login_time': login_time
                }
            }
            return jwt.encode(
                payload,
                channel_conf(const.HTTP).get('http_auth_secret_key'),
                algorithm='HS256'
            )  # 加密生成字符串
        except Exception as e:
            return e

    @staticmethod
    def decode_auth_token(auth_token):
        """
        验证Token
        :param auth_token: str
        :return: json|str
        """
        try:
            # 取消过期时间验证
            options = {'verify_exp': False}
            payload = jwt.decode(auth_token, channel_conf(const.HTTP).get(
                'http_auth_secret_key'), algorithms='HS256', options=options)
            if ('data' in payload and 'id' in payload['data']):
                return payload
            else:
                raise jwt.InvalidTokenError
        except jwt.ExpiredSignatureError:
            return 'Token Expired'
        except jwt.InvalidTokenError:
            return 'Invalid Token'


def authenticate(username, password):
    """
    用户登录，登录成功返回token
    :param username: str
    :param password: str
    :return: str|boolean
    """
    myredis = RedisSingleton()
    authPassword = myredis.redis.hget('sfbot:'+username, 'password')
    if authPassword is None:
        return False
    elif (authPassword.decode() != password):
        return False
    else:
        login_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        token = Auth.encode_auth_token(username, password, login_time)
        return token


def identify(request):
    """
    用户鉴权
    :return: boolean,str
    """
    try:
        if (request is None):
            return False, None
        authorization = request.cookies.get('Authorization')
        if not authorization:
            authorization = request.headers.get('Authorization')
        if (authorization):
            payload = Auth.decode_auth_token(authorization)
            if not isinstance(payload, str):
                username = payload['data']['id']
                password = payload['data']['password']
                myredis = RedisSingleton()
                authPassword = myredis.redis.hget('sfbot:'+username, 'password')
                if authPassword is None:
                    return False, None
                elif (authPassword.decode() != password):
                    return False, None
                else:
                    return True, username
        return False, None
 
    except jwt.ExpiredSignatureError:
        #result = 'Token已更改，请重新登录获取'
        return False, None
 
    except jwt.InvalidTokenError:
        #result = '没有提供认证token'
        return False, None
