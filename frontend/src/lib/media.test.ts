import { describe, expect, it } from 'vitest'
import { mediaKindOf, scanMedia } from './media'

const urls = (text: string) => scanMedia(text).map((m) => [m.kind, m.url, m.form])

describe('mediaKindOf', () => {
  it('按扩展名判断，忽略查询串与大小写', () => {
    expect(mediaKindOf('https://a.com/x.PNG')).toBe('image')
    expect(mediaKindOf('https://a.com/x.mp4?sig=1#t=2')).toBe('video')
    expect(mediaKindOf('https://a.com/x.mov')).toBe('video')
    expect(mediaKindOf('https://a.com/x.html')).toBeNull()
  })
})

describe('scanMedia', () => {
  it('markdown 图片、<img>、<video>', () => {
    expect(urls('看 ![图](https://a.com/1.png) 和 <img src="https://a.com/2.jpg" width=100> 以及 <video src=\'https://a.com/3.mp4\' controls></video>')).toEqual([
      ['image', 'https://a.com/1.png', 'markdown'],
      ['image', 'https://a.com/2.jpg', 'html'],
      ['video', 'https://a.com/3.mp4', 'html'],
    ])
  })

  it('markdown 图片的扩展名是视频时按视频处理；没有扩展名的仍按图片', () => {
    expect(urls('![](https://a.com/v.webm) ![](https://a.com/img?id=3)')).toEqual([
      ['video', 'https://a.com/v.webm', 'markdown'],
      ['image', 'https://a.com/img?id=3', 'markdown'],
    ])
  })

  it('裸 URL：支持全部扩展名，带查询串的签名链接，句末标点不算链接', () => {
    const text = [
      '图片 https://a.com/a.png。',
      '再来 https://a.com/b.jpeg, 还有 https://a.com/c.webp.',
      'gif https://a.com/d.gif 视频 https://a.com/e.mp4?X-Sig=abc&t=1 (https://a.com/f.mov)',
    ].join('\n')
    expect(urls(text).map((u) => u[1])).toEqual([
      'https://a.com/a.png',
      'https://a.com/b.jpeg',
      'https://a.com/c.webp',
      'https://a.com/d.gif',
      'https://a.com/e.mp4?X-Sig=abc&t=1',
      'https://a.com/f.mov',
    ])
  })

  it('markdown 图片 / 标签里的 URL 不会被当成裸 URL 重复识别', () => {
    expect(scanMedia('![](https://a.com/1.png)')).toHaveLength(1)
    expect(scanMedia('<img src="https://a.com/1.png">')).toHaveLength(1)
  })

  it('不是媒体的链接不识别', () => {
    expect(scanMedia('https://a.com/page.html https://a.com/x.png.zip https://a.com/readme 以及 a.png')).toEqual([])
  })

  it('区间对应原文', () => {
    const text = '前 https://a.com/a.png 后'
    const [m] = scanMedia(text)
    expect(text.slice(m.start, m.end)).toBe('https://a.com/a.png')
  })
})
