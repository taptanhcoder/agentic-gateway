import logging
import signal
from threading import Event


def main():
    stopped = Event()

    def stop(signum, frame):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO, format='{"event":"%(message)s"}')
    logging.info("worker_started")
    stopped.wait()
    logging.info("worker_stopped")


if __name__ == "__main__":
    main()
