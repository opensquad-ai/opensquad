export function shouldSkipLateFinalEvent(entries, finalText, messageId) {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    if (entry.kind !== 'message') continue;
    const existing = entry.data || {};
    if (existing.role === 'user') break;
    if (existing.role === 'assistant') {
      if (existing.content === finalText) return true;
      if (messageId && existing.message_id && existing.message_id === messageId) return true;
    }
  }

  if (messageId) {
    return entries.some((entry) =>
      entry.kind === 'message' && entry.data && entry.data.message_id === messageId
    );
  }
  return false;
}

export function appendMessageEntry(entries, message, getIdentityKey, genUID) {
  const key = getIdentityKey(message);
  if (key) {
    const exists = entries.some((entry) =>
      entry.kind === 'message' && getIdentityKey(entry.data) === key
    );
    if (exists) return entries;
  }
  return [...entries, { kind: 'message', data: message, _uid: genUID() }];
}

export function queueBufferedMessage(bufferedMessages, message, getIdentityKey) {
  const key = getIdentityKey(message);
  if (key) {
    const exists = bufferedMessages.some((item) => getIdentityKey(item) === key);
    if (exists) return bufferedMessages;
  }
  return [...bufferedMessages, message];
}

export function flushBufferedMessages(entries, bufferedMessages, getIdentityKey, genUID) {
  let next = [...entries];
  for (const message of bufferedMessages) {
    next = appendMessageEntry(next, message, getIdentityKey, genUID);
  }
  return next;
}

export function shouldHydrateOnCurrentSession(viewingHistorySession, previousSid, incomingSid, sessionBootstrapDone) {
  if (viewingHistorySession) return false;
  return incomingSid !== previousSid || !sessionBootstrapDone;
}

export function shouldHydrateOnHistorySync(viewingHistorySession, currentSessionId, incomingSid, sessionBootstrapDone) {
  if (viewingHistorySession) return false;
  if (!incomingSid || !currentSessionId) return true;
  if (!sessionBootstrapDone) return true;
  return incomingSid === currentSessionId;
}
