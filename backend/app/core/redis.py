# Redis Client
import redis.asyncio as aioredis
from app.core.config import get_settings

settings = get_settings()

redis_client: aioredis.Redis | None = None


async def init_redis():
    """Initialize the async Redis client."""
    global redis_client
    redis_client = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD or None,
        db=settings.REDIS_DB,
        decode_responses=True,
    )
    await redis_client.ping()
    return redis_client


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency: returns the Redis client."""
    if redis_client is None:
        await init_redis()
    return redis_client


async def close_redis():
    """Close the Redis connection on shutdown."""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None