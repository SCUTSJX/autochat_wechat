"""Coordinate UI preparation, outgoing receipt and cleanup as one desktop task."""
import time


def deliver_sticker(state, events, rpc, desktop_lock, conversation, keyword, options,
                    stopped=lambda: False, reset_cadence=True):
    task = state.begin(conversation, keyword)
    status, cleanup, receipt = 'transport_failed', 'unknown', None
    diagnostics = {}
    cadence_used = False
    with desktop_lock:
        try:
            if stopped():
                status, cleanup = 'cancelled', 'not_written'
                return dict(status=status, cleanup=cleanup)
            prepared = rpc(dict(op='sticker_prepare', task=task, conversation_id=conversation,
                                keyword=keyword, timeout=options['candidate_timeout_seconds']))
            diagnostics = prepared.get('diagnostics', {})
            if not prepared.get('ok') or not prepared.get('ready'):
                status = prepared.get('reason', 'prepare_failed')
                cleanup = prepared.get('cleanup', 'unknown')
                return dict(status=status, cleanup=cleanup, **({'diagnostics': diagnostics} if diagnostics else {}))
            # No desktop operation can interleave between this baseline and Enter.
            marker = events.mark()
            if stopped():
                status = 'cancelled'
            else:
                committed = rpc(dict(op='sticker_commit', task=task))
                cadence_used = committed.get('entered') is True
                diagnostics.update(committed.get('diagnostics', {}))
                if committed.get('ok') and committed.get('entered') and committed.get('conversation_id') == conversation:
                    status = 'unknown'
                    deadline = time.monotonic()+options['send_timeout_seconds']
                    while not stopped():
                        remaining = max(0, deadline-time.monotonic())
                        observed = events.wait(conversation, marker, min(0.25, remaining), stopped, keyword)
                        status, receipt = observed['status'], observed.get('message_id')
                        if status != 'unknown':
                            break
                        # The native hook may not emit a local text send. A
                        # newly visible keyword row + empty editor is a text
                        # fallback, not a reason to block the conversation.
                        visible = rpc(dict(op='sticker_status', task=task))
                        if visible.get('keyword_text_seen'):
                            status = 'unexpected_text'
                            diagnostics['text_confirmation'] = 'window_readback'
                            break
                        if time.monotonic() >= deadline:
                            break
                else:
                    # A worker exception can occur after key injection; only an
                    # explicit entered=False is evidence that Enter was not used.
                    status = 'not_sent' if committed.get('entered') is False else 'unknown'
            finished = rpc(dict(op='sticker_finish', task=task))
            diagnostics.update(finished.get('diagnostics', {}))
            if finished.get('window_restore_error'):
                diagnostics['window_restore_error'] = finished['window_restore_error']
            if finished.get('error'):
                diagnostics['cleanup_error'] = str(finished['error'])[:300]
            if status == 'unknown' and finished.get('keyword_text_seen'):
                status = 'unexpected_text'
            cleanup = finished.get('cleanup', 'unknown')
            return dict(status=status, cleanup=cleanup, **({'diagnostics': diagnostics} if diagnostics else {}))
        except Exception:
            # Never retry Return or replay the successful text message.
            # A later cleanup/transport failure cannot revoke a receipt we
            # already observed. Its draft status can still block further UI.
            if status != 'confirmed':
                status = 'transport_failed'
            try:
                result = rpc(dict(op='sticker_finish', task=task))
                cleanup = result.get('cleanup', 'unknown')
            except Exception:
                cleanup = 'unknown'
            raise
        finally:
            state.finish(task, status, cleanup, receipt, reset_cadence=reset_cadence,
                         cadence_used=cadence_used)
