import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        from markflow.selfcheck import run
        raise SystemExit(run(sys.argv[2] if len(sys.argv) > 2 else "自检结果"))
    else:
        from markflow.app import main
        raise SystemExit(main())
