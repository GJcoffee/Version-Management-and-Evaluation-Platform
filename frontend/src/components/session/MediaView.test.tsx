import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Markdown } from './Markdown'
import { MediaText, MediaView } from './MediaView'

const IMG = 'https://cdn.example.com/a.png'
const VID = 'https://cdn.example.com/v.mp4'

describe('Markdown 中的媒体', () => {
  it('markdown 图片渲染为图片，并在下方显示原始链接', () => {
    render(<Markdown>{`好了 ![猫](${IMG})`}</Markdown>)
    const media = screen.getByTestId('media')
    expect(media).toHaveAttribute('data-kind', 'image')
    expect(within(media).getByRole('img')).toHaveAttribute('src', IMG)
    expect(within(media).getByTestId('media-link')).toHaveTextContent(IMG)
  })

  it('<img> / <video> 标签渲染为图片 / 播放器（闭合标签不留下残渣）', () => {
    const { container } = render(
      <Markdown>{`<img src="${IMG}">\n\n<video src="${VID}" controls></video>\n\n文字 <video src="${VID}"></video> 之后`}</Markdown>,
    )
    const media = screen.getAllByTestId('media')
    expect(media.map((m) => m.getAttribute('data-kind'))).toEqual(['image', 'video', 'video'])
    expect(container.querySelector('video')).toHaveAttribute('src', VID)
    expect(container.textContent).not.toContain('</video>')
    expect(container.textContent).toContain('之后')
  })

  it('裸 URL：原文链接保留在文字里，媒体紧随其后，不重复显示链接', () => {
    const { container } = render(<Markdown>{`图在这：${IMG} 视频：${VID}。`}</Markdown>)
    expect(container.querySelector('p')?.textContent).toContain(`图在这：${IMG}`)
    expect(screen.getAllByTestId('media').map((m) => m.getAttribute('data-kind'))).toEqual(['image', 'video'])
    expect(screen.queryByTestId('media-link')).toBeNull()
  })

  it('markdown 链接里的 URL 保持为普通链接，普通文字不受影响', () => {
    render(<Markdown>{`[原图](${IMG}) 和 **粗体**`}</Markdown>)
    expect(screen.queryByTestId('media')).toBeNull()
    expect(screen.getByRole('link', { name: '原图' })).toHaveAttribute('href', IMG)
    expect(screen.getByText('粗体').tagName).toBe('STRONG')
  })

  it('![](x.mp4) 渲染为视频', () => {
    render(<Markdown>{`![](${VID})`}</Markdown>)
    expect(screen.getByTestId('media')).toHaveAttribute('data-kind', 'video')
  })
})

describe('MediaView', () => {
  it('点击图片放大，点击预览或按 Esc 关闭', async () => {
    render(<MediaView kind="image" src={IMG} />)
    expect(screen.queryByTestId('lightbox')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '放大查看图片' }))
    const box = screen.getByTestId('lightbox')
    expect(within(box).getByRole('img')).toHaveAttribute('src', IMG)
    await userEvent.click(box)
    expect(screen.queryByTestId('lightbox')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '放大查看图片' }))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByTestId('lightbox')).toBeNull()
  })

  it('视频带播放控件', () => {
    const { container } = render(<MediaView kind="video" src={VID} />)
    expect(container.querySelector('video')).toHaveAttribute('controls')
  })
})

describe('MediaText', () => {
  it('保留原文，并把文中的媒体列在下面', () => {
    const { container } = render(<MediaText text={`结果 ![](${IMG}) 与 ${VID}`} />)
    expect(container.textContent).toContain(`![](${IMG}) 与 ${VID}`)
    const gallery = screen.getByTestId('media-gallery')
    expect(within(gallery).getAllByTestId('media')).toHaveLength(2)
    expect(within(gallery).queryByTestId('media-link')).toBeNull()
  })

  it('没有媒体时不渲染图库', () => {
    render(<MediaText text="只有文字" />)
    expect(screen.queryByTestId('media-gallery')).toBeNull()
  })
})
