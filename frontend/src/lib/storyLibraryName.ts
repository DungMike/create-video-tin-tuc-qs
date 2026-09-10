// Tên thư viện theo mẫu "xx-yy <tên>" (ví dụ "21-25 kenh viet"): cặp số đầu tên
// cho biết dải kênh mà thư viện phục vụ. Nhờ mẫu chung này danh sách xếp được
// theo số và nhìn lướt là biết thư viện nào của kênh nào.
//
// Bản sao phía client của src/utils/story_library.py::normalize_library_name —
// chỉ để báo lỗi ngay khi gõ; backend vẫn là nơi chuẩn hóa thật sự khi lưu.

export const LIBRARY_NAME_HINT = 'Đặt tên theo mẫu "xx-yy tên thư viện", ví dụ: 21-25 kenh viet.';
export const LIBRARY_NAME_PLACEHOLDER = "21-25 kenh viet";

// Cặp số ở đầu tên, ngăn cách bằng khoảng trắng hoặc dấu gạch/gạch dưới/gạch chéo.
const RANGE_PREFIX = /^(\d{1,4})(?:\s*[-\u2010-\u2015_/]\s*|\s+)(\d{1,4})(?!\d)\s*[-\u2010-\u2015_.:]?\s*(.*)$/;
// Cặp số bị kẹp sau vài chữ ("kenh 31 35 Thai Tam linh"): kéo nó về đầu tên.
const RANGE_ANYWHERE = /(?<!\d)(\d{1,4})\s*[-\u2010-\u2015_/ ]\s*(\d{1,4})(?!\d)/;
const TRIM_CHARS = /^[\s\-\u2010-\u2015_.:]+|[\s\-\u2010-\u2015_.:]+$/g;

/** Tên đã chuẩn hóa, hoặc null khi không suy ra được cặp số / phần mô tả. */
export function normalizeLibraryName(name: string): string | null {
  const raw = name.replace(/\s+/g, " ").trim();
  if (!raw) return null;

  let start: string;
  let end: string;
  let rest: string;

  const prefix = RANGE_PREFIX.exec(raw);
  if (prefix) {
    [, start, end, rest] = prefix;
  } else {
    const anywhere = RANGE_ANYWHERE.exec(raw);
    if (!anywhere) return null;
    [, start, end] = anywhere;
    rest = `${raw.slice(0, anywhere.index)} ${raw.slice(anywhere.index + anywhere[0].length)}`;
  }

  rest = rest.replace(/\s+/g, " ").replace(TRIM_CHARS, "");
  return rest ? `${start}-${end} ${rest}` : null;
}
