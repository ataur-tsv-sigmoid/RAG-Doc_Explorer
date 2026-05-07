from apscheduler.schedulers.background import BackgroundScheduler

from app.services.conversation_service import (
    ConversationService
)


scheduler = BackgroundScheduler()


def start_cleanup_scheduler():

    scheduler.add_job(
        func=ConversationService.cleanup_old_conversations,
        trigger="interval",
        hours=12,
        kwargs={"days": 7},
        id="conversation_cleanup",
        replace_existing=True,
    )

    scheduler.start()

    print("✅ Conversation cleanup scheduler started")