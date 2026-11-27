import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { cva, type VariantProps } from 'class-variance-authority';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { X } from 'lucide-react';
export function cn(...values:ClassValue[]){return twMerge(clsx(values));}
const buttonVariants=cva('button',{variants:{variant:{default:'button-primary',outline:'button-outline',ghost:'button-ghost'},size:{default:'',icon:'button-icon',sm:'button-small'}},defaultVariants:{variant:'default',size:'default'}});
export const Button=React.forwardRef<HTMLButtonElement,React.ButtonHTMLAttributes<HTMLButtonElement>&VariantProps<typeof buttonVariants>&{asChild?:boolean}>(({className,variant,size,asChild=false,...props},ref)=>{const Comp=asChild?Slot:'button';return <Comp ref={ref} className={cn(buttonVariants({variant,size}),className)} {...props}/>});Button.displayName='Button';
export function Badge({children,tone='neutral'}:{children:React.ReactNode,tone?:string}){return <span className={`badge badge-${tone}`}>{children}</span>}
export function Card({className,...props}:React.HTMLAttributes<HTMLDivElement>){return <div className={cn('card',className)} {...props}/>}
export const Input=React.forwardRef<HTMLInputElement,React.InputHTMLAttributes<HTMLInputElement>>(({className,...props},ref)=><input ref={ref} className={cn('input',className)} {...props}/>);Input.displayName='Input';
export const Dialog=DialogPrimitive.Root;
export const DialogTrigger=DialogPrimitive.Trigger;
export function DialogContent({children,title,description,className}:{children:React.ReactNode,title:string,description:string,className?:string}){return <DialogPrimitive.Portal><DialogPrimitive.Overlay className="dialog-overlay"/><DialogPrimitive.Content className={cn('dialog-content',className)}><div className="dialog-heading"><div><DialogPrimitive.Title>{title}</DialogPrimitive.Title><DialogPrimitive.Description>{description}</DialogPrimitive.Description></div><DialogPrimitive.Close asChild><Button variant="ghost" size="icon" aria-label="Đóng"><X size={18}/></Button></DialogPrimitive.Close></div>{children}</DialogPrimitive.Content></DialogPrimitive.Portal>}
export const Tabs=TabsPrimitive.Root;
export const TabsContent=TabsPrimitive.Content;
export const TabsList=React.forwardRef<React.ElementRef<typeof TabsPrimitive.List>,React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>>(({className,...props},ref)=><TabsPrimitive.List ref={ref} className={cn('tabs-list',className)} {...props}/>);TabsList.displayName='TabsList';
export const TabsTrigger=React.forwardRef<React.ElementRef<typeof TabsPrimitive.Trigger>,React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>>(({className,...props},ref)=><TabsPrimitive.Trigger ref={ref} className={cn('tabs-trigger',className)} {...props}/>);TabsTrigger.displayName='TabsTrigger';
