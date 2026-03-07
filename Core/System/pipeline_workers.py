# pipeline_workers.py: Concrete worker implementations for the LeoBook pipeline.
# Part of LeoBook Core — System
#
# Classes: StartupWorker, PrologueWorker, Chapter1Worker, Chapter2Worker
# Called by: Leo.py via Supervisor

import logging
from Core.System.worker_base import BaseWorker

logger = logging.getLogger(__name__)

class StartupWorker(BaseWorker):
    def __init__(self):
        super().__init__("Startup")
        
    async def execute(self):
        from Leo import run_startup_sync
        return await run_startup_sync()

class PrologueWorker(BaseWorker):
    def __init__(self):
        super().__init__("Prologue")
        
    async def execute(self):
        from Leo import run_prologue_p1, run_prologue_p2, run_prologue_p3
        await run_prologue_p1()
        await run_prologue_p2()
        await run_prologue_p3()
        return True

class Chapter1Worker(BaseWorker):
    def __init__(self, playwright_instance):
        super().__init__("Chapter 1")
        self.p = playwright_instance
        
    async def execute(self, scheduler):
        from Leo import run_chapter_1_p1, run_chapter_1_p2, run_chapter_1_p3
        fb_healthy = await run_chapter_1_p1(self.p)
        await run_chapter_1_p2(self.p, scheduler=scheduler)
        await run_chapter_1_p3()
        return fb_healthy

class Chapter2Worker(BaseWorker):
    def __init__(self, playwright_instance):
        super().__init__("Chapter 2")
        self.p = playwright_instance
        
    async def execute(self):
        from Leo import run_chapter_2_p1, run_chapter_2_p2
        await run_chapter_2_p1(self.p)
        await run_chapter_2_p2(self.p)
        return True
