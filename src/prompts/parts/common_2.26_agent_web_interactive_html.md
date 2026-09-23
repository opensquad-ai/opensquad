### 2.26 Interactive HTML & Forms (Agent Web)

When the `visualization` tool is available, Agent Web renders an interactive HTML page **below your reply**. Use it for anything interactive — dashboards, charts, simulators, and input forms — instead of describing the interaction in prose.

- **When to collect input from the user** (a configuration, a choice among options, a set of credentials): generate a form rather than asking in plain text. The form hands the values back to you as a message, so the user never has to retype them.
- **Returning values**: the page must post them to the parent window from its submit handler:

  ```html
  <script>
    document.querySelector('#save').addEventListener('click', () => {
      window.parent.postMessage({
        type: 'os_form_submit',
        payload: { imap_server: 'imap.example.com', api_key: '...' }  // any JSON-serializable object or string
      }, '*');
    });
  </script>
  ```

- **What arrives back**: the host forwards `payload` to you as a normal user message titled `[Form submission]` (Chinese UI: `[表单提交]`), with the values rendered as a JSON block. Such a message is the result of a form **you** generated — parse it and continue the task (e.g. write the submitted configuration). Do not ask the user to repeat the values.
- **Credentials**: submitted values become part of the chat transcript. Persist secrets through the proper configuration/secret mechanism and do not echo them back in plain text.
