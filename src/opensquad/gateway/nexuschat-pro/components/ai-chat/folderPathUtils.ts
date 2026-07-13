/**
 * Resolve a directory path from a native folder-picker selection (webkitdirectory / Electron).
 * Re-exports shared helpers from cwdRecents for component-level imports.
 */

export {
  directoryPathFromFileList,
  folderNameHintFromFileList,
} from '../../utils/cwdRecents';
