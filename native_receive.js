// Observe natural calls only: no NativeFunction calls, DB access or key reads.
const profile = __PROFILE__;
const binary = __BINARY__;
const layout = profile.receiver_layout;
if (!layout || (!layout.verified && !profile.calibration_mode)) throw new Error('layout not verified');
const module = Process.enumerateModules().find(m => m.path === binary);
if (!module) throw new Error('exact module missing');
function address(c) {
    const p = module.base.add(c.rva-profile.minimum_load_vaddr);
    const actual = new Uint8Array(p.readByteArray(c.prefix.length));
    if (!c.prefix.every((v,i) => actual[i] === v)) throw new Error('prefix mismatch');
    return p;
}
function readable(p, n) {
    const range = Process.findRangeByAddress(p);
    return range && range.protection.includes('r') && p.add(n).compare(range.base.add(range.size)) <= 0;
}
function stringAt(record, offset, limit) {
    const p = record.add(offset);
    if (!readable(p,24)) throw new Error('string header');
    const flag = p.readU8(), long = (flag & 1) !== 0;
    const n = long ? Number(p.add(8).readU64().toString()) : flag >>> 1;
    if (!Number.isSafeInteger(n) || n < 0 || n > limit || (!long && n > 22)) throw new Error('string length');
    if (!n) return '';
    const data = long ? p.add(16).readPointer() : p.add(1);
    if (!readable(data,n)) throw new Error('string data');
    return data.readUtf8String(n);
}
const batches = new Map();
let failed = false;
const startedUnix = Math.floor(Date.now()/1000);
function fatal() {if (!failed) {failed=true; send({type:'fatal'});}}
const sync = profile.candidates.find(c => c.name === 'AddMessageListToDBForSync');
const syncAddress = address(sync), directionAddress = address(profile.direction_accessor);
Interceptor.attach(syncAddress, {
    onEnter(args) {
        this.pushed=false;
        if (failed) return;
        try {
            const h=args[2]; if (!readable(h,24)) throw new Error('batch header');
            const begin=h.readPointer(), end=h.add(8).readPointer(), cap=h.add(16).readPointer();
            const bytes=Number(end.sub(begin).toString()), capacity=Number(cap.sub(begin).toString());
            if (!Number.isSafeInteger(bytes) || !Number.isSafeInteger(capacity) || bytes < 0 ||
                bytes > layout.stride*4096 || capacity < bytes || capacity > layout.stride*65536 ||
                bytes%layout.stride || capacity%layout.stride) throw new Error('batch bounds');
            const stack=batches.get(this.threadId)||[];
            stack.push({begin,end,seen:new Set()}); batches.set(this.threadId,stack); this.pushed=true;
        } catch (_) {fatal();}
    },
    onLeave() {
        if (!this.pushed) return;
        const stack=batches.get(this.threadId); stack.pop(); if (!stack.length) batches.delete(this.threadId);
    }
});
Interceptor.attach(directionAddress, {
    onEnter(args) {
        this.record=null; this.batch=null;
        if (failed) return;
        const stack=batches.get(this.threadId), batch=stack && stack[stack.length-1];
        if (!batch) return; // Historical UI reads are never emitted.
        const p=args[0];
        if (p.compare(batch.begin)<0 || p.compare(batch.end)>=0) return;
        const delta=Number(p.sub(batch.begin).toString());
        if (!Number.isSafeInteger(delta) || delta%layout.stride) return;
        this.record=p; this.batch=batch;
    },
    onLeave(retval) {
        if (!this.record || failed) return;
        try {
            const own=retval.toInt32() & 255;
            if (own!==0 && own!==1) throw new Error('unknown direction');
            const p=this.record, key=p.toString();
            if (this.batch.seen.has(key)) return;
            this.batch.seen.add(key);
            if (!readable(p,layout.stride)) throw new Error('record bounds');
            const rawType=p.add(layout.type_offset).readU32();
            // Image type 3 is experimental and enabled only by the diagnostic entry.
            const nontext=rawType!==1 && profile.accept_nontext===true && !profile.calibration_mode;
            const image=rawType===3 && (profile.accept_images===true || nontext) && !profile.calibration_mode;
            if (rawType!==1 && !image && !nontext) return;
            const created=p.add(layout.create_time_offset).readU32();
            if (created<=startedUnix) return; // Includes history synced after attaching.
            if (created>Math.floor(Date.now()/1000)+120) throw new Error('invalid message time');
            let from, to;
            try {from=stringAt(p,layout.from_offset,256); to=stringAt(p,layout.to_offset,256);}
            catch (error) {if (nontext) return; throw error;}
            if (!from || !to || from === to) {
                if (nontext) return; // Skip system records without conversational participants.
                throw new Error('invalid participants');
            }
            const self=profile.calibration_proof && profile.calibration_proof.self_id;
            const groupIds=[from,to].filter(value => value.endsWith('@chatroom'));
            if (groupIds.length>1) throw new Error('ambiguous group identity');
            const group=groupIds.length===1 ? groupIds[0] : null;
            if (self && (group
                    ? ((own===1 && from!==self) || (own===0 && from===self)
                       || (own===0 && from===group && to!==self))
                    : ((own===0 && (to!==self || from===self))
                       || (own===1 && (from!==self || to===self)))))
                throw new Error('direction identity mismatch');
            if (profile.all_windows && own===1 && profile.accept_own_messages!==true) return;
            const conversation=group || (own===1 ? to : from);
            const id=p.add(layout.server_id_offset).readU64().toString();
            if (!conversation || id==='0') {
                if (nontext) return;
                throw new Error('missing identity');
            }
            let body='', imageFields=[];
            if (image || nontext) {
                // These string slots are known for text, not verified media paths.
                // A failed optional read must not stop subsequent text reception.
                for (const offset of [...new Set([layout.body_offset,72,304])]) {
                    try {
                        const value=stringAt(p,offset,65536);
                        imageFields.push({offset,read_ok:true,value});
                        if (offset===layout.body_offset) body=value;
                    } catch (_) {
                        imageFields.push({offset,read_ok:false,error:'unreadable_or_not_utf8_string'});
                    }
                }
            } else {
                body=stringAt(p,layout.body_offset,65536);
                if (!body.trim()) return;
            }
            if (profile.calibration_mode) {
                if (profile.automatic_peer) {
                    if (!self || own!==0 || from!==profile.automatic_peer || to!==self) return;
                } else if (!Array.isArray(profile.calibration_tokens)
                           || !profile.calibration_tokens.includes(body)) return;
            }
            const event={type:'message',message_id:id,conversation_id:conversation,from_id:from,to_id:to,
                  direction:own===1?'outgoing':'incoming',message_kind:image?'image':(nontext?'nontext':'text'),
                  conversation_kind:group?'group':'private',text:body,created_at:created};
            if (image) Object.assign(event,{raw_type:rawType,image_fields:imageFields,
                media_layout_verified:false});
            else if (nontext) Object.assign(event,{raw_type:rawType,media_layout_verified:false,media_fields:imageFields});
            send(event);
        } catch (_) {fatal();}
    }
});
send({type:'ready'});
