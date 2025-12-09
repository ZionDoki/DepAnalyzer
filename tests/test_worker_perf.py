"""Tests for Worker performance and correctness."""

import time
import unittest
from threading import Thread
from depanalyzer.runtime.worker import Worker, Task, TaskPriority

class TestWorker(unittest.TestCase):
    def test_worker_condition_wakeup(self):
        """Test that worker wakes up immediately via condition variable."""
        worker = Worker(max_workers=2)
        
        # Enqueue tasks from a separate thread after a short delay
        def enqueue_later():
            time.sleep(0.5)
            worker.enqueue(Task("t1", lambda: "result1"))
            
        t = Thread(target=enqueue_later)
        t.start()
        
        start_time = time.time()
        # This should block until t1 arrives, then process it and return
        results = worker.run_all()
        duration = time.time() - start_time
        
        t.join()
        
        self.assertIn("t1", results)
        self.assertTrue(results["t1"].success)
        # Should be close to 0.5s (task delay) + 0.5s (idle wait) + overhead
        # Allowing up to 1.5s to account for thread scheduling/overhead
        self.assertLess(duration, 1.5) 

    def test_worker_load(self):
        """Test worker under load."""
        worker = Worker(max_workers=4)
        count = 100
        
        for i in range(count):
            # Fix: capture i as default argument x=i
            worker.enqueue(Task(f"t{i}", lambda x=i: x * x))
            
        results = worker.run_all()
        self.assertEqual(len(results), count)
        for i in range(count):
            self.assertEqual(results[f"t{i}"].result, i * i)

if __name__ == "__main__":
    unittest.main()
