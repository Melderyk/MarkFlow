import sys

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] in {"--self-test", "--self-test-stress"}:
        from markflow.selfcheck import run
        source=sys.argv[3] if sys.argv[1]=='--self-test-stress' and len(sys.argv)>3 else None
        raise SystemExit(run(sys.argv[2] if len(sys.argv) > 2 else "自检结果", source))
    else:
        from markflow.app import main
        raise SystemExit(main())
