// Element Plus 交互反馈统一封装，替代原生 alert / confirm / prompt
import { ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

export function toastSuccess(message) {
  return ElMessage.success(message)
}

export function toastError(message) {
  return ElMessage.error(message)
}

export function toastInfo(message) {
  return ElMessage.info(message)
}

export function toastWarning(message) {
  return ElMessage.warning(message)
}

/**
 * 确认框。返回 true=确认，false=取消。
 */
export async function confirmDialog(message, title = '提示', type = 'warning') {
  try {
    await ElMessageBox.confirm(message, title, {
      type,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      closeOnClickModal: false
    })
    return true
  } catch (e) {
    return false
  }
}

/**
 * 输入框。返回输入值；取消/关闭返回 null。
 */
export async function promptDialog(message, initialValue = '', title = '请输入') {
  try {
    const { value } = await ElMessageBox.prompt(message, title, {
      inputValue: initialValue,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      closeOnClickModal: false
    })
    return value == null ? '' : String(value)
  } catch (e) {
    return null
  }
}
