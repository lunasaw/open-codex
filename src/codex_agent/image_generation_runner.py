"""Apply configured SDK headers only inside the bundled image script subprocess."""
import json
import os
import runpy
import sys

if not __package__:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_agent.image_generation import request_headers


def run():
    headers = json.loads(os.environ.pop('CODEX_IMAGE_HEADERS', '{}'))
    script = sys.argv.pop(1)
    sys.argv[0] = script
    namespace = runpy.run_path(script)
    main = namespace['main']
    for factory_name in ('_create_client', '_create_async_client'):
        original = main.__globals__[factory_name]

        def factory(original=original):
            client = original()
            for method_name in ('generate', 'edit'):
                method = getattr(client.images, method_name)

                def request(*args, method=method, **kwargs):
                    # Batch jobs and explicit --model options may select a
                    # different model. Resolve the route for each API request.
                    extra = request_headers({'headers': headers, 'model': kwargs['model']})
                    kwargs['extra_headers'] = {**extra, **kwargs.get('extra_headers', {})}
                    return method(*args, **kwargs)

                setattr(client.images, method_name, request)
            return client

        main.__globals__[factory_name] = factory
    raise SystemExit(main())


if __name__ == '__main__':
    run()
