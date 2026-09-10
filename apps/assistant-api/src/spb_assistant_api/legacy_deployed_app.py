"""Explicit old-Docker entry; normal deployed_app never changes syscall filters."""


def main():
    from .security.legacy_threads import install_thread_compatibility_filter

    install_thread_compatibility_filter()
    # Deliberately defer application imports until the single-thread bootstrap ends.
    from .deployed_app import main as run_deployment

    run_deployment()


if __name__ == "__main__":
    main()
