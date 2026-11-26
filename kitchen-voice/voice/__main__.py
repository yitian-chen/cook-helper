import argparse
import json
import sys
import time
import uuid
from .config import RUNTIME

def main():
    parser = argparse.ArgumentParser(description='厨房语音模块：分段测试与完整链路')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('devices')
    commands.add_parser('providers', help='显示当前服务商和缺失的配置名称，不显示密钥、不调用云端')
    record = commands.add_parser('record')
    record.add_argument('--seconds', type=float, default=5)
    asr = commands.add_parser('asr'); asr.add_argument('file')
    agent = commands.add_parser('agent'); agent.add_argument('text')
    tts = commands.add_parser('tts'); tts.add_argument('text')
    play = commands.add_parser('play'); play.add_argument('file')
    pipeline = commands.add_parser('pipeline'); pipeline.add_argument('file')
    pipeline.add_argument('--no-play', action='store_true')
    commands.add_parser('serve')
    args = parser.parse_args()
    if args.command == 'providers':
        from .providers import status
        print(json.dumps({kind: status(kind) for kind in ('asr', 'tts')}, ensure_ascii=False, indent=2))
        return
    if args.command == 'serve':
        import uvicorn
        uvicorn.run('voice.api:app', host='127.0.0.1', port=8765)
        return
    if args.command == 'devices':
        from .audio import sd_module
        print(sd_module().query_devices())
        return
    if args.command == 'record':
        from .audio import Recorder
        if not 0 < args.seconds <= 60:
            raise ValueError('录音时长须为0～60秒')
        recorder = Recorder()
        recorder.start()
        print('正在录音……', flush=True)
        try:
            time.sleep(args.seconds)
        finally:
            result = {'file': str(recorder.stop())}
    elif args.command == 'asr':
        from .cloud import transcribe
        result = {'text': transcribe(args.file)}
    elif args.command == 'agent':
        from .agent import ask_agent
        result = ask_agent(args.text, uuid.uuid4().hex)
    elif args.command == 'tts':
        from .cloud import synthesize
        result = {'file': str(synthesize(args.text))}
    elif args.command == 'play':
        from .audio import Player, sd_module
        Player().play(args.file)
        sd_module().wait()
        result = {'status': 'finished'}
    else:
        from .service import VoiceService
        result = VoiceService().process(args.file, not args.no_play)
        if not args.no_play:
            from .audio import sd_module
            sd_module().wait()
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('已中止', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        from .providers import CloudServiceError
        print(f'失败（{type(exc).__name__}）：请检查配置、依赖、网络和音频设备。', file=sys.stderr)
        if isinstance(exc, (ValueError, FileNotFoundError, TimeoutError, CloudServiceError)):
            print(str(exc), file=sys.stderr)
        sys.exit(1)
