# backend/core/security.py
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv, find_dotenv
from passlib.context import CryptContext
from jose import jwt, JWTError

load_dotenv(find_dotenv(usecwd=True))

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY environment variable is required. Set it in your .env file.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 门票有效期：7天

# 实例化 bcrypt 加密器 (将明文密码转化为不可逆的哈希密文)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与数据库里的密文是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """对明文密码进行不可逆哈希加密"""
    return pwd_context.hash(password)

def create_access_token(data: dict) -> str:
    """生成 JWT 数字门票"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    # 使用密钥和 HS256 算法进行签名
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt