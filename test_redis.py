import redis
import os
from config.settings import REDIS_CONFIG

def test_connection():
    try:
        # 使用配置字典创建连接
        client = redis.Redis(**REDIS_CONFIG)
        # 发送PING命令
        response = client.ping()
        if response:
            print("✅ Redis远程连接成功！")
            # 可选：打印一些服务器信息
            info = client.info('server')
            print(f"   Redis版本：{info.get('redis_version')}")
        else:
            print("❌ 收到非预期响应。")
    except redis.AuthenticationError as e:
        print(f"❌ Redis认证失败（密码错误）: {e}")
    except redis.ConnectionError as e:
        print(f"❌ 无法连接到Redis服务器（网络/地址/端口问题）: {e}")
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")

if __name__ == '__main__':
    test_connection()