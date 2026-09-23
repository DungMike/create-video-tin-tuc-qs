import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Cấu trúc `<tên audio>.chapters.txt` — hiển thị ngay cạnh ô chọn file để không phải
 * tra tài liệu, kèm nút tải file mẫu (sinh tại chỗ, không cần gọi backend).
 */
export const CHAPTER_FILE_SAMPLE = `# File chương cho <tên audio>.mp3 — đặt cạnh file audio/.srt, cùng tên gốc.
# Dòng chương: mốc | tiêu đề | nhãn (tuỳ chọn) | câu trích (tuỳ chọn)
# Dòng bắt đầu bằng ">" là câu nhấn; bỏ trống chữ thì lấy câu phụ đề tại mốc đó.
# Dòng bắt đầu bằng "#" là ghi chú, hệ thống bỏ qua.
00:00 | หมู่บ้านริมโขง | บทนำ | ความเงียบสงบที่เราเห็นนั้น บางครั้งก็เป็นเพียงฉากหน้า
01:01 | ป้าบัว หญิงทอเสื่อ | เบื้องหลัง | ป้าบัวอาศัยอยู่ที่นี่เพียงลำพังมาหลายปีแล้ว
02:58 | คืนสุดท้ายที่วัด | เหตุการณ์
04:59 | การหายตัวอย่างปริศนา | การค้นหา | เจ้าของจักรยานคันนี้... หายตัวไปไหน
07:48 | กลิ่นปริศนาใต้ศาลา | การค้นพบ
> 00:59
> 05:36 ไม่มีใครตอบ
> 08:16
`;

export function ChapterFileHelp({ className = "" }: { className?: string }) {
  const download = () => {
    const url = URL.createObjectURL(new Blob([CHAPTER_FILE_SAMPLE], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "mau.chapters.txt";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <details className={`rounded-lg border border-border/70 bg-background/60 p-3 ${className}`}>
      <summary className="cursor-pointer text-xs font-medium text-foreground">
        Cấu trúc file chương (.chapters.txt) và file mẫu
      </summary>
      <div className="mt-2 grid gap-2 text-xs text-muted-foreground">
        <ul className="grid list-disc gap-1 pl-4">
          <li>
            Tên file: <code>&lt;tên audio&gt;.chapters.txt</code> — ví dụ <code>tap-01.mp3</code> đi với{" "}
            <code>tap-01.chapters.txt</code>. Để cạnh audio/.srt thì nút "Thư mục (Audio + SRT + chương)" tự ghép.
          </li>
          <li>
            Mỗi chương một dòng: <code>mốc | tiêu đề | nhãn | câu trích</code>. Nhãn và câu trích bỏ trống được.
          </li>
          <li>
            Mốc viết <code>mm:ss</code> hoặc <code>hh:mm:ss</code>. Mốc được kéo về đầu dòng phụ đề gần nhất để
            không cắt giữa câu.
          </li>
          <li>
            Dòng bắt đầu bằng <code>&gt;</code> là câu nhấn (kiểu "Câu nói điểm nhấn"): <code>&gt; mốc chữ</code>;
            bỏ trống chữ thì lấy chính câu phụ đề tại mốc đó.
          </li>
          <li>
            Dòng bắt đầu bằng <code>#</code> là ghi chú. Trong tiêu đề, <code>\N</code> là chỗ xuống dòng.
          </li>
          <li>
            <strong>Không có file chương thì video không hiện chữ chương</strong> (tên chương, thẻ chương, thẻ
            chuyển chương đều tắt); chương chỉ còn là mốc thời gian để các bố cục đổi cảnh.
          </li>
        </ul>
        <pre className="overflow-x-auto rounded bg-muted/60 p-2 font-mono text-[11px] leading-5 text-foreground">
          {CHAPTER_FILE_SAMPLE}
        </pre>
        <div>
          <Button type="button" variant="outline" size="sm" onClick={download}>
            <Download className="mr-2 size-4" />
            Tải file mẫu
          </Button>
        </div>
      </div>
    </details>
  );
}
