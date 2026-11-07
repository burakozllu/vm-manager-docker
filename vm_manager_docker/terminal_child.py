"""Attach a controlling TTY in a single-threaded child, then execute SSH."""
import fcntl
import os
import sys
import termios


def main():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
