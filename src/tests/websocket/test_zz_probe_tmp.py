def test_probe():
    import inspect
    import src.api.websocket.session as sm

    src = inspect.getsource(sm.SessionManager.resume_session)
    print("MODULE:", sm.__file__)
    print("HAS PATCH:", "old_conn_is_live" in src)
